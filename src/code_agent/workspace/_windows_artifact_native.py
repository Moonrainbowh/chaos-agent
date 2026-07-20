from __future__ import annotations

import ctypes
from ctypes import wintypes

from ._windows_artifact_handles import (
    IoStatusBlock,
    kernel_function,
    nt_function,
    raise_for_status,
)


class UnicodeString(ctypes.Structure):
    _fields_ = (
        ("length", wintypes.USHORT),
        ("maximum_length", wintypes.USHORT),
        ("buffer", wintypes.LPWSTR),
    )


class ObjectAttributes(ctypes.Structure):
    _fields_ = (
        ("length", wintypes.ULONG),
        ("root_directory", wintypes.HANDLE),
        ("object_name", ctypes.POINTER(UnicodeString)),
        ("attributes", wintypes.ULONG),
        ("security_descriptor", wintypes.LPVOID),
        ("security_quality", wintypes.LPVOID),
    )


def create_relative_file(parent_handle: int, name: str) -> int:
    buffer, text = _unicode_string(name)
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        parent_handle,
        ctypes.pointer(text),
        0x40,
        None,
        None,
    )
    status_block = IoStatusBlock()
    handle = wintypes.HANDLE()
    function = nt_function("NtCreateFile")
    function.argtypes = (
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
    )
    function.restype = wintypes.LONG
    status = function(
        ctypes.byref(handle),
        0x110102,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        0x80,
        0x7,
        2,
        0x60,
        None,
        0,
    )
    del buffer
    raise_for_status(status)
    return int(handle.value)


def write_file(handle: int, content: bytes) -> None:
    function = kernel_function("WriteFile")
    function.argtypes = (
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    function.restype = wintypes.BOOL
    offset = 0
    while offset < len(content):
        chunk = content[offset : offset + 64 * 1024]
        written = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(chunk)
        if not function(handle, buffer, len(chunk), ctypes.byref(written), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value == 0:
            raise OSError("snapshot artifact write made no progress")
        offset += written.value


def rename_relative(handle: int, parent_handle: int, target_name: str) -> None:
    payload = _rename_payload(parent_handle, target_name)
    status_block = IoStatusBlock()
    function = nt_function("NtSetInformationFile")
    function.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
    )
    function.restype = wintypes.LONG
    status = function(handle, ctypes.byref(status_block), payload, len(payload), 10)
    raise_for_status(status)


def mark_delete(handle: int) -> bool:
    delete = ctypes.c_ubyte(1)
    status_block = IoStatusBlock()
    function = nt_function("NtSetInformationFile")
    function.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
    )
    function.restype = wintypes.LONG
    status = function(
        handle,
        ctypes.byref(status_block),
        ctypes.byref(delete),
        ctypes.sizeof(delete),
        13,
    )
    return status >= 0


def _rename_payload(parent_handle: int, target_name: str) -> ctypes.Array[ctypes.c_char]:
    class RenameInformation(ctypes.Structure):
        _fields_ = (
            ("replace", ctypes.c_ubyte),
            ("root", wintypes.HANDLE),
            ("length", wintypes.ULONG),
            ("name", wintypes.WCHAR * 1),
        )

    encoded = target_name.encode("utf-16-le")
    offset = RenameInformation.name.offset
    payload = ctypes.create_string_buffer(offset + len(encoded))
    header = RenameInformation.from_buffer(payload)
    header.replace = 1
    header.root = parent_handle
    header.length = len(encoded)
    ctypes.memmove(ctypes.addressof(payload) + offset, encoded, len(encoded))
    return payload


def _unicode_string(value: str) -> tuple[ctypes.Array[ctypes.c_wchar], UnicodeString]:
    buffer = ctypes.create_unicode_buffer(value)
    length = len(value.encode("utf-16-le"))
    pointer = ctypes.cast(buffer, wintypes.LPWSTR)
    return buffer, UnicodeString(length, length + 2, pointer)
