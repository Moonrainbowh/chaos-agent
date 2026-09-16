"""Terminal dimensions with a Windows ConPTY-safe query."""
from __future__ import annotations

import os
import shutil
from collections import namedtuple

TerminalSize = namedtuple("terminal_size", "columns lines")


def terminal_size(fallback: tuple[int, int] = (100, 30)) -> TerminalSize:
    """Read the current console window, avoiding stale ``shutil`` values."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class Coord(ctypes.Structure):
                _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short)]

            class Rect(ctypes.Structure):
                _fields_ = [(name, ctypes.c_short) for name in ("left", "top", "right", "bottom")]

            class Info(ctypes.Structure):
                _fields_ = [("size", Coord), ("cursor", Coord), ("attributes", wintypes.WORD), ("window", Rect), ("maximum", Coord)]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetStdHandle.argtypes = [wintypes.DWORD]
            kernel.GetStdHandle.restype = wintypes.HANDLE
            kernel.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]
            kernel.GetConsoleScreenBufferInfo.restype = wintypes.BOOL
            info = Info()
            handle = kernel.GetStdHandle(-11)
            if handle and kernel.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
                return TerminalSize(info.window.right - info.window.left + 1, info.window.bottom - info.window.top + 1)
        except (AttributeError, OSError, TypeError, ValueError):
            pass
    try:
        size = shutil.get_terminal_size(fallback)
        return TerminalSize(max(1, size.columns), max(1, size.lines))
    except (OSError, ValueError):
        return TerminalSize(*fallback)
