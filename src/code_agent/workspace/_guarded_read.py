from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from .errors import WorkspaceError
from .paths import PathInput, WorkspacePathGuard


def read_guarded_file(
    path: PathInput, guard: WorkspacePathGuard, max_bytes: int
) -> bytes:
    """Read a regular file only after verifying the opened handle's identity."""
    expected = guard.resolve(path)
    descriptor: int | None = None
    try:
        descriptor = os.open(expected, _open_flags())
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise WorkspaceError(f"untracked path is not a regular file: {expected.name}")
        _verify_handle(descriptor, opened, expected, guard)
        return _read_bounded(descriptor, max_bytes)
    except WorkspaceError:
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot read untracked file: {expected.name}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _open_flags() -> int:
    flags = os.O_RDONLY
    if os.name == "posix":
        for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC"):
            flags |= getattr(os, name, 0)
    else:
        flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    return flags


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
