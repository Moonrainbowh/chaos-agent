from __future__ import annotations

import errno
import math
import os
import re
import stat
import time
from pathlib import Path
from types import TracebackType

from .errors import WorkspaceError
from ._worktree_paths import is_link_like, require_contained_unlinked


_REPOSITORY_ID = re.compile(r"[0-9a-f]{64}\Z")
_RETRY_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN, errno.EDEADLK})


class RepositoryLifecycleLock:
    """Bounded cross-process lock stored outside Git metadata."""

    def __init__(self, storage_root: Path, repository_id: str, timeout_s: float) -> None:
        if not valid_repository_id(repository_id):
            raise WorkspaceError("invalid repository id for lifecycle lock")
        validate_lock_timeout(timeout_s)
        self._storage_root = storage_root
        self._repository_id = repository_id
        self._timeout_s = float(timeout_s)
        self._fd: int | None = None

    def __enter__(self) -> RepositoryLifecycleLock:
        fd = _open_lock_file(self._storage_root, self._repository_id)
        try:
            _acquire(fd, self._timeout_s)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_type, traceback
        assert self._fd is not None
        try:
            _release(self._fd)
        except OSError as error:
            if exc is None:
                raise WorkspaceError(f"repository lifecycle lock release failed: {error}") from error
            if hasattr(exc, "add_note"):
                exc.add_note(f"repository lifecycle lock release failed: {error}")
        finally:
            os.close(self._fd)
            self._fd = None
        return False


def _open_lock_file(storage_root: Path, repository_id: str) -> int:
    repository_root = storage_root / repository_id
    require_contained_unlinked(storage_root, repository_root)
    repository_root.mkdir(exist_ok=True)
    require_contained_unlinked(storage_root, repository_root)
    lock_path = repository_root / ".lifecycle.lock"
    if is_link_like(lock_path):
        raise WorkspaceError("repository lifecycle lock cannot be a link or reparse point")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise WorkspaceError(f"repository lifecycle lock could not open: {error}") from error
    try:
        _verify_open_lock(fd, lock_path, storage_root)
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _verify_open_lock(fd: int, lock_path: Path, storage_root: Path) -> None:
    require_contained_unlinked(storage_root, lock_path)
    opened = os.fstat(fd)
    visible = lock_path.lstat()
    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
        visible.st_dev,
        visible.st_ino,
    ):
        raise WorkspaceError("repository lifecycle lock identity changed during open")


def _acquire(fd: int, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            _lock_nonblocking(fd)
            return
        except OSError as error:
            if error.errno not in _RETRY_ERRNOS:
                raise WorkspaceError(f"repository lifecycle lock failed: {error}") from error
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkspaceError("repository lifecycle lock timeout") from error
            time.sleep(min(0.01, remaining))


def _lock_nonblocking(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


def validate_lock_timeout(timeout_s: float) -> None:
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise TypeError("lock_timeout_s must be a number")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("lock_timeout_s must be positive and finite")


def valid_repository_id(repository_id: object) -> bool:
    return isinstance(repository_id, str) and bool(_REPOSITORY_ID.fullmatch(repository_id))
