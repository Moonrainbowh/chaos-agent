"""User-scoped DPAPI on Windows; private file permissions on Unix."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from .models import AuthError


class _Blob(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(value: bytes, *, decrypt: bool) -> bytes:
    buffer = ctypes.create_string_buffer(value)
    source = _Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        ok = crypt.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)
        )
    else:
        ok = crypt.CryptProtectData(
            ctypes.byref(source), "Chaos Agent", None, None, None, 1,
            ctypes.byref(output),
        )
    if not ok:
        raise AuthError("Cannot access Windows user credential protection")
    try:
        return ctypes.string_at(output.data, output.length)
    finally:
        kernel.LocalFree(output.data)


def protect(value: bytes) -> bytes:
    return b"CHAOS-DPAPI-1\n" + _crypt(value, decrypt=False) if os.name == "nt" else value


def unprotect(value: bytes) -> bytes:
    prefix = b"CHAOS-DPAPI-1\n"
    if value.startswith(prefix):
        if os.name != "nt":
            raise AuthError("Windows credentials must be opened by their Windows user")
        return _crypt(value[len(prefix):], decrypt=True)
    if os.name == "nt":
        raise AuthError("Credential file is not protected with Windows DPAPI")
    return value
