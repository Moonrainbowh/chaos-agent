from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from .errors import WorkspaceError


_OPEN_REPARSE_POINT = 0x00200000
_BACKUP_SEMANTICS = 0x02000000
_REPARSE_ATTRIBUTE = 0x400
_DIRECTORY_ATTRIBUTE = 0x10
_SHARE_READ = 0x1
_SHARE_ALL = 0x7


class _WindowsOpenFailure(Exception):
    def __init__(self, error_code: int, path: Path) -> None:
        super().__init__(f"Win32 open failed ({error_code}): {path.name}")
        self.error_code = error_code
        self.path = path


class _WindowsLeafMissingError(WorkspaceError):
    """The leaf was absent while its verified parent chain was held."""


def open_guarded_file(expected: Path, root: Path) -> int:
    """Open a leaf while verified parent directory handles prevent replacement."""
    parent_handles: list[int] = []
    descriptor: int | None = None
    try:
        for parent in _parent_paths(expected, root):
            parent_handles.append(_open_parent(parent))
        descriptor = _open_leaf(expected)
    except BaseException:
        _close_all(parent_handles)
        raise
    close_error = _close_all(parent_handles)
    if close_error is not None:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise WorkspaceError("cannot close guarded parent directory") from close_error
    return descriptor


def _parent_paths(expected: Path, root: Path) -> tuple[Path, ...]:
    try:
        relative = expected.relative_to(root)
        current = root
    except ValueError:
        current = Path(expected.anchor)
        relative = expected.relative_to(current)
    parents = [current]
    for part in relative.parts[:-1]:
        current = current / part
        parents.append(current)
    return tuple(parents)


def _open_parent(path: Path) -> int:
    try:
        handle = _create_handle(
            path,
            desired_access=0x80,
            share_mode=_SHARE_READ,
            flags=_OPEN_REPARSE_POINT | _BACKUP_SEMANTICS,
        )
    except _WindowsOpenFailure as error:
        raise WorkspaceError(
            f"cannot open guarded parent directory: {path.name}"
        ) from error
    try:
        attributes = _handle_attributes(handle)
        final_path = _final_handle_path(handle)
        if not attributes & _DIRECTORY_ATTRIBUTE or attributes & _REPARSE_ATTRIBUTE:
            raise WorkspaceError(f"guarded parent is unsafe: {path.name}")
        if os.path.normcase(str(final_path)) != os.path.normcase(str(path)):
            raise WorkspaceError(f"guarded parent resolved elsewhere: {path.name}")
    except BaseException as error:
        _close_after_failure(handle)
        if isinstance(error, OSError) and not isinstance(error, WorkspaceError):
            raise WorkspaceError(
                f"cannot inspect guarded parent directory: {path.name}"
            ) from error
        raise
    return handle


def _open_leaf(expected: Path) -> int:
    try:
        handle = _create_handle(
            expected,
            desired_access=0x80000000,
            share_mode=_SHARE_ALL,
            flags=_OPEN_REPARSE_POINT | _BACKUP_SEMANTICS,
        )
    except _WindowsOpenFailure as error:
        if error.error_code in (2, 3):
            raise _WindowsLeafMissingError(
                f"guarded file is missing: {expected.name}"
            ) from error
        raise WorkspaceError(f"cannot open guarded leaf: {expected.name}") from error
    try:
        attributes = _handle_attributes(handle)
        if attributes & _REPARSE_ATTRIBUTE or attributes & _DIRECTORY_ATTRIBUTE:
            raise WorkspaceError(f"guarded file handle is unsafe: {expected.name}")
        return _handle_to_descriptor(handle)
    except BaseException as error:
        _close_after_failure(handle)
        if isinstance(error, OSError) and not isinstance(error, WorkspaceError):
            raise WorkspaceError(
                f"cannot inspect guarded file handle: {expected.name}"
            ) from error
        raise


def _create_handle(
    path: Path,
    *,
    desired_access: int,
    share_mode: int,
    flags: int,
) -> int:
    function = _create_file_function()
    function.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    function.restype = wintypes.HANDLE
    handle = function(str(path), desired_access, share_mode, None, 3, flags, None)
    if handle != wintypes.HANDLE(-1).value:
        return int(handle)
    raise _WindowsOpenFailure(ctypes.get_last_error(), path)


def _create_file_function():
    return ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW


def _handle_attributes(handle: int) -> int:
    class FileInformation(ctypes.Structure):
        _fields_ = (
            ("attributes", wintypes.DWORD),
            ("creation", wintypes.FILETIME),
            ("access", wintypes.FILETIME),
            ("write", wintypes.FILETIME),
            ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        )

    information = FileInformation()
    function = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).GetFileInformationByHandle
    function.argtypes = (wintypes.HANDLE, ctypes.POINTER(FileInformation))
    function.restype = wintypes.BOOL
    if not function(handle, ctypes.byref(information)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(information.attributes)


def _final_handle_path(handle: int) -> Path:
    function = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).GetFinalPathNameByHandleW
    function.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    function.restype = wintypes.DWORD
    required = function(handle, None, 0, 0)
    if required == 0:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(required + 1)
    copied = function(handle, buffer, len(buffer), 0)
    if copied == 0 or copied >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(_strip_device_prefix(buffer.value))


def _strip_device_prefix(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def _handle_to_descriptor(handle: int) -> int:
    import msvcrt

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOINHERIT", 0)
    return msvcrt.open_osfhandle(handle, flags)


def _close_handle(handle: int) -> None:
    function = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    function.argtypes = (wintypes.HANDLE,)
    function.restype = wintypes.BOOL
    if not function(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _close_after_failure(handle: int) -> None:
    try:
        _close_handle(handle)
    except OSError:
        pass


def _close_all(handles: list[int]) -> OSError | None:
    first_error: OSError | None = None
    for handle in reversed(handles):
        try:
            _close_handle(handle)
        except OSError as error:
            if first_error is None:
                first_error = error
    return first_error
