from __future__ import annotations

import ctypes
import hashlib
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import NoReturn

from ._windows_handle_cleanup import attach_cleanup, close_all

_Identity = tuple[int, int, int]
_DELETE = 0x00010000
_GENERIC_READ = 0x80000000
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_ALL = 0x00000007
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_DISPOSITION_INFO_CLASS = 4
_NT_FILE_RENAME_INFORMATION = 10
_MISSING_WINERRORS = frozenset({2, 3})


class _FileTime(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("created", _FileTime),
        ("accessed", _FileTime),
        ("written", _FileTime),
        ("volume", wintypes.DWORD),
        ("size_high", wintypes.DWORD),
        ("size_low", wintypes.DWORD),
        ("links", wintypes.DWORD),
        ("index_high", wintypes.DWORD),
        ("index_low", wintypes.DWORD),
    ]


class _FileDispositionInformation(ctypes.Structure):
    _fields_ = [("delete_file", wintypes.BOOL)]


class _IoStatusBlock(ctypes.Structure):
    _fields_ = [("status", wintypes.LONG), ("information", ctypes.c_size_t)]


_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
_CREATE_FILE = _KERNEL32.CreateFileW
_CREATE_FILE.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
_CREATE_FILE.restype = wintypes.HANDLE
_GET_FILE_INFORMATION = _KERNEL32.GetFileInformationByHandle
_GET_FILE_INFORMATION.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_ByHandleFileInformation),
]
_GET_FILE_INFORMATION.restype = wintypes.BOOL
_READ_FILE = _KERNEL32.ReadFile
_READ_FILE.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
_READ_FILE.restype = wintypes.BOOL
_SET_FILE_INFORMATION = _KERNEL32.SetFileInformationByHandle
_SET_FILE_INFORMATION.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
]
_SET_FILE_INFORMATION.restype = wintypes.BOOL
_CLOSE_HANDLE = _KERNEL32.CloseHandle
_CLOSE_HANDLE.argtypes = [wintypes.HANDLE]
_CLOSE_HANDLE.restype = wintypes.BOOL
_NTDLL = ctypes.WinDLL("ntdll")
_NT_SET_INFORMATION = _NTDLL.NtSetInformationFile
_NT_SET_INFORMATION.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_IoStatusBlock),
    wintypes.LPVOID,
    wintypes.ULONG,
    wintypes.ULONG,
]
_NT_SET_INFORMATION.restype = wintypes.LONG
_RTL_STATUS_TO_ERROR = _NTDLL.RtlNtStatusToDosError
_RTL_STATUS_TO_ERROR.argtypes = [wintypes.LONG]
_RTL_STATUS_TO_ERROR.restype = wintypes.ULONG
_INVALID_HANDLE = wintypes.HANDLE(-1).value


def path_identity(path: Path) -> _Identity:
    handle = _open(path, _FILE_READ_ATTRIBUTES, _FILE_SHARE_ALL, directory=True)
    try:
        return _identity(handle)
    finally:
        _close(handle)


def cleanup_exact(
    path: Path,
    expected: _Identity,
    replaced_error: type[OSError],
) -> None:
    try:
        handle = _open(
            path,
            _DELETE | _FILE_READ_ATTRIBUTES,
            _FILE_SHARE_ALL,
        )
    except OSError as error:
        if getattr(error, "winerror", None) in _MISSING_WINERRORS:
            return
        raise
    try:
        _require_identity(handle, expected, replaced_error)
        disposition = _FileDispositionInformation(True)
        if not _SET_FILE_INFORMATION(
            handle,
            _FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            _raise_last_error()
    finally:
        _close(handle)


def publish_no_replace(
    source: Path,
    expected: _Identity,
    parent_expected: _Identity,
    destination: Path,
    verifier: Callable[[Path], None],
    replaced_error: type[OSError],
) -> None:
    checked = destination.resolve(strict=False)
    parent = _open(
        checked.parent,
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        directory=True,
    )
    guard: int | None = None
    publisher: int | None = None
    published = False
    primary: BaseException | None = None
    try:
        try:
            _require_identity(parent, parent_expected, replaced_error)
            guard = _open(source, _GENERIC_READ, _FILE_SHARE_READ)
            _require_identity(guard, expected, replaced_error)
            verifier(source)
            baseline = _sha256_handle(guard)
            _close(guard)
            guard = None

            publisher = _open(
                source,
                _GENERIC_READ | _DELETE | _FILE_READ_ATTRIBUTES,
                _FILE_SHARE_READ,
            )
            _require_identity(publisher, expected, replaced_error)
            if _sha256_handle(publisher) != baseline:
                raise replaced_error(
                    "owned temporary content changed before publication"
                )
            _before_windows_publish(publisher, source, checked)
            _rename_relative(publisher, parent, checked.name)
            published = True
        except BaseException as error:
            primary = error
            raise
        finally:
            cleanup = close_all((publisher, guard, parent), _close)
            if cleanup is not None:
                if primary is None:
                    raise cleanup
                attach_cleanup(primary, cleanup)
    except BaseException as error:
        if published:
            setattr(error, "publication_committed", True)
            setattr(error, "source_consumed", True)
        raise


def _before_windows_publish(handle: int, source: Path, destination: Path) -> None:
    del handle, source, destination


def _open(path: Path, access: int, share: int, *, directory: bool = False) -> int:
    flags = _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= _FILE_FLAG_BACKUP_SEMANTICS
    handle = _CREATE_FILE(
        str(path), access, share, None, _OPEN_EXISTING, flags, None
    )
    if handle == _INVALID_HANDLE:
        _raise_last_error()
    return int(handle)


def _identity(handle: int) -> _Identity:
    information = _ByHandleFileInformation()
    if not _GET_FILE_INFORMATION(handle, ctypes.byref(information)):
        _raise_last_error()
    return (
        int(information.volume),
        (int(information.index_high) << 32) | int(information.index_low),
        (int(information.created.high) << 32) | int(information.created.low),
    )


def _require_identity(
    handle: int,
    expected: _Identity,
    replaced_error: type[OSError],
) -> None:
    if _identity(handle) != expected:
        raise replaced_error(
            "owned temporary path was replaced; foreign file was preserved"
        )


def _sha256_handle(handle: int) -> bytes:
    digest = hashlib.sha256()
    while True:
        buffer = ctypes.create_string_buffer(64 * 1024)
        count = wintypes.DWORD()
        if not _READ_FILE(
            handle,
            buffer,
            len(buffer),
            ctypes.byref(count),
            None,
        ):
            _raise_last_error()
        if count.value == 0:
            return digest.digest()
        digest.update(buffer.raw[: count.value])


def _rename_relative(handle: int, parent: int, name: str) -> None:
    payload = _rename_payload(parent, name)
    status_block = _IoStatusBlock()
    status = _NT_SET_INFORMATION(
        handle,
        ctypes.byref(status_block),
        payload,
        len(payload),
        _NT_FILE_RENAME_INFORMATION,
    )
    if status < 0:
        raise ctypes.WinError(_RTL_STATUS_TO_ERROR(status))


def _rename_payload(parent: int, name: str) -> ctypes.Array[ctypes.c_char]:
    class _FileRenameInformation(ctypes.Structure):
        _fields_ = [
            ("replace", ctypes.c_ubyte),
            ("root", wintypes.HANDLE),
            ("length", wintypes.DWORD),
            ("name", wintypes.WCHAR * 1),
        ]

    encoded = name.encode("utf-16-le")
    offset = _FileRenameInformation.name.offset
    payload = ctypes.create_string_buffer(offset + len(encoded))
    header = _FileRenameInformation.from_buffer(payload)
    header.replace = 0
    header.root = parent
    header.length = len(encoded)
    ctypes.memmove(ctypes.addressof(payload) + offset, encoded, len(encoded))
    return payload


def _close(handle: int) -> None:
    if not _CLOSE_HANDLE(handle):
        _raise_last_error()


def _raise_last_error() -> NoReturn:
    raise ctypes.WinError(ctypes.get_last_error())
