from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from .errors import WorkspaceError


_FILE_CASE_SENSITIVE_INFO = 23
_FILE_CS_FLAG_CASE_SENSITIVE_DIR = 0x1


def directory_is_case_sensitive(path: Path) -> bool:
    if os.name != "nt":
        return True
    from ._windows_artifact_handles import close_handle, open_directory

    class CaseSensitiveInfo(ctypes.Structure):
        _fields_ = (("flags", wintypes.ULONG),)

    handle = open_directory(path)
    try:
        info = CaseSensitiveInfo()
        function = ctypes.WinDLL(
            "kernel32", use_last_error=True
        ).GetFileInformationByHandleEx
        function.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        function.restype = wintypes.BOOL
        if not function(
            handle,
            _FILE_CASE_SENSITIVE_INFO,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise WorkspaceError(
                f"cannot determine Windows directory case sensitivity: {path}"
            )
        return bool(info.flags & _FILE_CS_FLAG_CASE_SENSITIVE_DIR)
    finally:
        close_handle(handle)
