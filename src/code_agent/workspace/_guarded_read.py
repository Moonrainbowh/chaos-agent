from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from ._windows_guarded_open import _WindowsLeafMissingError, open_guarded_file
from .errors import FileTooLargeError, WorkspaceError
from .paths import PathInput, WorkspacePathGuard


class _GuardedFileMissingError(WorkspaceError):
    """The initial open found no file at the already guarded path."""


def read_guarded_file(
    path: PathInput,
    guard: WorkspacePathGuard,
    max_bytes: int,
    *,
    reject_known_oversize: bool = False,
) -> bytes:
    """Read a regular file only after verifying the opened handle's identity."""
    expected = guard.resolve(path)
    descriptor: int | None = None
    try:
        try:
            descriptor = _open_existing(expected, guard)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise WorkspaceError(
                    f"untracked path is not a regular file: {expected.name}"
                )
            _verify_handle(descriptor, opened, expected, guard)
            if reject_known_oversize and opened.st_size > max_bytes:
                raise FileTooLargeError(
                    f"file exceeds {max_bytes} bytes: {expected.name}"
                )
            content = _read_bounded(descriptor, max_bytes)
        except WorkspaceError:
            raise
        except OSError as error:
            raise WorkspaceError(
                f"cannot read untracked file: {expected.name}"
            ) from error
    except BaseException:
        _close_descriptor(descriptor, suppress_error=True)
        raise
    _close_descriptor(descriptor, suppress_error=False)
    return content


def _open_existing(expected: Path, guard: object) -> int:
    if os.name == "nt":
        root = guard.root if hasattr(guard, "root") else guard
        root_identity = (
            getattr(guard, "root_identity", None)
            if _is_within(expected, root)
            else None
        )
        try:
            return open_guarded_file(expected, root, root_identity)
        except _WindowsLeafMissingError as error:
            raise _GuardedFileMissingError(str(error)) from error
    try:
        return os.open(expected, _open_flags())
    except FileNotFoundError as error:
        raise _GuardedFileMissingError(
            f"guarded file is missing: {expected.name}"
        ) from error


def _close_descriptor(descriptor: int | None, *, suppress_error: bool) -> None:
    if descriptor is None:
        return
    try:
        os.close(descriptor)
    except OSError as error:
        if not suppress_error:
            raise WorkspaceError("cannot close guarded file") from error


def _open_flags() -> int:
    flags = os.O_RDONLY
    if os.name == "posix":
        for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC"):
            flags |= getattr(os, name, 0)
    else:
        flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    return flags


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _verify_handle(
    descriptor: int,
    opened: os.stat_result,
    expected: Path,
    guard: WorkspacePathGuard,
) -> None:
    final_path = _final_handle_path(descriptor)
    verified = guard.resolve(final_path if final_path is not None else expected)
    if os.path.normcase(str(verified)) != os.path.normcase(str(expected)):
        raise WorkspaceError(f"untracked file changed while opening: {expected.name}")
    current = verified.lstat()
    if not os.path.samestat(opened, current):
        raise WorkspaceError(f"untracked file changed while opening: {expected.name}")


def _final_handle_path(descriptor: int) -> Path | None:
    if os.name == "nt":
        return _windows_handle_path(descriptor)
    if sys.platform.startswith("linux"):
        try:
            return Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        except OSError:
            return None
    return None


def _windows_handle_path(descriptor: int) -> Path:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    function = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    function.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    function.restype = wintypes.DWORD
    handle = msvcrt.get_osfhandle(descriptor)
    required = function(handle, None, 0, 0)
    if required == 0:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(required + 1)
    copied = function(handle, buffer, len(buffer), 0)
    if copied == 0 or copied >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(_strip_windows_device_prefix(buffer.value))


def _strip_windows_device_prefix(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _read_bounded(descriptor: int, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining:
        chunk = os.read(descriptor, min(remaining, 64 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
