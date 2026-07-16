from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from .errors import WorkspaceError


_ERROR_INVALID_FUNCTION = 1
_ERROR_ACCESS_DENIED = 5


class IoStatusBlock(ctypes.Structure):
    _fields_ = (("status", ctypes.c_ssize_t), ("information", ctypes.c_size_t))


def open_directory(path: Path) -> int:
    function = kernel_function("CreateFileW")
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
    handle = function(str(path), 0x80, 0x3, None, 3, 0x02200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(handle)


def directory_identity(path: Path) -> tuple[int, ...]:
    handle = open_directory(path)
    try:
        if os.path.normcase(str(_final_path(handle))) != os.path.normcase(str(path)):
            raise WorkspaceError("snapshot artifact directory resolved elsewhere")
        return _handle_identity(handle)
    finally:
        close_handle(handle)


def verify_directory(
    handle: int, expected_path: Path, expected_identity: tuple[int, ...]
) -> None:
    final = _final_path(handle)
    same_path = os.path.normcase(str(final)) == os.path.normcase(str(expected_path))
    if not same_path or _handle_identity(handle) != expected_identity:
        raise WorkspaceError("snapshot artifact directory identity changed")


def flush_file(handle: int) -> None:
    function = kernel_function("FlushFileBuffers")
    function.argtypes = (wintypes.HANDLE,)
    function.restype = wintypes.BOOL
    if not function(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def flush_directory(handle: int) -> bool:
    function = kernel_function("FlushFileBuffers")
    function.argtypes = (wintypes.HANDLE,)
    function.restype = wintypes.BOOL
    if function(handle):
        return True
    error = ctypes.get_last_error()
    if error in (_ERROR_INVALID_FUNCTION, _ERROR_ACCESS_DENIED):
        return False
    raise ctypes.WinError(error)


def close_handle(handle: int) -> None:
    function = kernel_function("CloseHandle")
    function.argtypes = (wintypes.HANDLE,)
    function.restype = wintypes.BOOL
    function(handle)


def raise_for_status(status: int) -> None:
    if status < 0:
        converter = nt_function("RtlNtStatusToDosError")
        converter.argtypes = (wintypes.LONG,)
        converter.restype = wintypes.ULONG
        raise ctypes.WinError(converter(status))


def kernel_function(name: str):
    return getattr(ctypes.WinDLL("kernel32", use_last_error=True), name)


def nt_function(name: str):
    return getattr(ctypes.WinDLL("ntdll", use_last_error=True), name)


def _handle_identity(handle: int) -> tuple[int, ...]:
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
    function = kernel_function("GetFileInformationByHandle")
    function.argtypes = (wintypes.HANDLE, ctypes.POINTER(FileInformation))
    function.restype = wintypes.BOOL
    if not function(handle, ctypes.byref(information)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not information.attributes & 0x10 or information.attributes & 0x400:
        raise WorkspaceError("snapshot artifact directory handle is unsafe")
    return information.volume, information.index_high, information.index_low


def _final_path(handle: int) -> Path:
    function = kernel_function("GetFinalPathNameByHandleW")
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
