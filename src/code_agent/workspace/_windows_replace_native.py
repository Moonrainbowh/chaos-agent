from __future__ import annotations

import ctypes
import os
from pathlib import Path

from ctypes import wintypes

from . import _secure_io as safety
from ._windows_guarded_open import (
    _OPEN_REPARSE_POINT,
    _WindowsOpenFailure,
    _close_handle,
    _create_handle,
    _final_handle_path,
)
from .errors import WorkspaceError


GENERIC_READ = 0x80000000
DELETE = 0x00010000
FILE_READ_ATTRIBUTES = 0x80
FILE_WRITE_ATTRIBUTES = 0x100
_SHARE_READ_DELETE = 0x1 | 0x4
_FILE_ATTRIBUTE_READONLY = 0x1
_FILE_ATTRIBUTE_NORMAL = 0x80
_REPLACEFILE_WRITE_THROUGH = 0x1


class _FileBasicInfo(ctypes.Structure):
    _fields_ = (
        ("creation_time", ctypes.c_int64),
        ("last_access_time", ctypes.c_int64),
        ("last_write_time", ctypes.c_int64),
        ("change_time", ctypes.c_int64),
        ("attributes", wintypes.DWORD),
    )


def close_handle(handle: int) -> None:
    _close_handle(handle)


def open_file_guard(path: Path, desired_access: int) -> int:
    try:
        return _create_handle(
            path,
            desired_access=desired_access,
            share_mode=_SHARE_READ_DELETE,
            flags=_OPEN_REPARSE_POINT,
        )
    except _WindowsOpenFailure as error:
        raise ctypes.WinError(error.error_code)


def require_file_identity(
    handle: int, path: Path, expected: safety.PathIdentity
) -> None:
    final = _final_handle_path(handle)
    if os.path.normcase(str(final)) != os.path.normcase(str(path)):
        raise WorkspaceError(f"protected file resolved elsewhere: {path}")
    if file_identity(handle) != (expected.device, expected.inode):
        raise WorkspaceError(f"protected file identity changed: {path}")


def file_identity(handle: int) -> tuple[int, int]:
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
    if information.attributes & (0x10 | 0x400):
        raise WorkspaceError("protected file handle is not a plain file")
    index = (int(information.index_high) << 32) | int(information.index_low)
    return int(information.volume), index


def set_handle_readonly(handle: int, readonly: bool) -> None:
    get_info = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).GetFileInformationByHandleEx
    get_info.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    get_info.restype = wintypes.BOOL
    info = _FileBasicInfo()
    if not get_info(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    attributes = int(info.attributes)
    if readonly:
        attributes = (
            attributes & ~_FILE_ATTRIBUTE_NORMAL
        ) | _FILE_ATTRIBUTE_READONLY
    else:
        attributes &= ~_FILE_ATTRIBUTE_READONLY
        if attributes == 0:
            attributes = _FILE_ATTRIBUTE_NORMAL
    info.attributes = attributes
    set_info = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).SetFileInformationByHandle
    set_info.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_info.restype = wintypes.BOOL
    if not set_info(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())


def replace_file(target: Path, temporary: Path, backup: Path) -> None:
    try:
        backup.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ctypes.WinError(80)
    function = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    function.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    )
    function.restype = wintypes.BOOL
    if not function(
        str(target),
        str(temporary),
        str(backup),
        _REPLACEFILE_WRITE_THROUGH,
        None,
        None,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
