from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Mapping

from . import _posix_io
from . import _secure_io as safety
from ._secure_posix import inspect_at
from .errors import WorkspaceError
from .paths import WorkspacePathGuard


TEMP_PREFIX = ".code-agent-edit-"


def raw_identity_from_fd(
    descriptor: int, target: Path
) -> safety.PathIdentity | None:
    del target
    try:
        identity = safety.identity_from_stat(os.fstat(descriptor))
    except OSError:
        return None
    if not safety.is_regular(identity) or _has_reparse(identity):
        return None
    return identity


def remove_posix_temp(
    parent_fd: int, name: str, expected: safety.PathIdentity | None
) -> None:
    if expected is None:
        raise _ownership_failure(name)
    try:
        current = inspect_at(parent_fd, name, missing_ok=True, context="restore")
        if current is None:
            return
        if current != expected:
            raise _ownership_failure(name)
        _posix_io.unlink(parent_fd, name)
        remaining = inspect_at(parent_fd, name, missing_ok=True, context="restore")
        if remaining is not None:
            raise WorkspaceError(f"cannot clean restore temporary file: {name}")
    except WorkspaceError:
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot clean restore temporary file: {name}") from error


def remove_windows_temp(
    path: Path,
    expected: safety.PathIdentity | None,
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
) -> None:
    if path.parent != state.target.parent or not path.name.startswith(TEMP_PREFIX):
        raise _ownership_failure(str(path))
    safety.verify_parent_state(state.parent, guard, created, context="restore")
    if expected is None:
        raise _ownership_failure(str(path))
    try:
        current = _inspect_windows_temp(path)
        if current is None:
            return
        if current != expected:
            raise _ownership_failure(str(path))
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        path.unlink()
        if _inspect_windows_temp(path) is not None:
            raise WorkspaceError(f"cannot clean restore temporary file: {path}")
    except WorkspaceError:
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot clean restore temporary file: {path}") from error


def attach_or_raise_cleanup(
    primary: BaseException | None, cleanup: WorkspaceError
) -> None:
    if primary is None:
        raise cleanup
    setattr(primary, "cleanup_error", cleanup)
    add_note = getattr(primary, "add_note", None)
    if callable(add_note):
        add_note(f"restore cleanup failure: {cleanup}")


def _inspect_windows_temp(path: Path) -> safety.PathIdentity | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    identity = safety.identity_from_stat(metadata)
    if stat.S_ISLNK(identity.mode) or _has_reparse(identity):
        raise _ownership_failure(str(path))
    if not safety.is_regular(identity):
        raise _ownership_failure(str(path))
    return identity


def _has_reparse(identity: safety.PathIdentity) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(identity.attributes & reparse)


def _ownership_failure(label: str) -> WorkspaceError:
    return WorkspaceError(f"cleanup ownership failure: {label}")
