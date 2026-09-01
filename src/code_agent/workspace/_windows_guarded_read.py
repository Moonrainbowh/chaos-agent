from __future__ import annotations

import ctypes
import msvcrt
from ctypes import wintypes


_READ_CHUNK_BYTES = 64 * 1024


def read_bounded_windows_file(descriptor: int, max_bytes: int) -> bytes:
    """Read via Win32 so byte-range lock errors retain their native code."""
    handle = msvcrt.get_osfhandle(descriptor)
    function = ctypes.WinDLL("kernel32", use_last_error=True).ReadFile
    function.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    function.restype = wintypes.BOOL
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining:
        requested = min(remaining, _READ_CHUNK_BYTES)
        buffer = ctypes.create_string_buffer(requested)
        read = wintypes.DWORD()
        if not function(handle, buffer, requested, ctypes.byref(read), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if read.value == 0:
            break
        chunks.append(buffer.raw[: read.value])
        remaining -= read.value
    return b"".join(chunks)
