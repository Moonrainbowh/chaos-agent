"""Preserve shortcuts before msvcrt strips Windows console modifier flags."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import time


class _KeyEvent(ctypes.Structure):
    _fields_ = [("down", wintypes.BOOL), ("repeat", wintypes.WORD),
                ("virtual_key", wintypes.WORD), ("scan", wintypes.WORD),
                ("character", wintypes.WCHAR), ("modifiers", wintypes.DWORD)]


class _EventData(ctypes.Union):
    _fields_ = [("key", _KeyEvent), ("padding", ctypes.c_byte * 16)]


class _InputRecord(ctypes.Structure):
    _fields_ = [("kind", wintypes.WORD), ("data", _EventData)]


def read_character(console: object) -> str:
    if os.name == "nt" and getattr(console, "__name__", "") == "msvcrt":
        # Wait before peeking: getwch would otherwise consume the event that
        # arrives later, losing its modifier flags in Windows Terminal/ConPTY.
        while not console.kbhit():
            time.sleep(.001)
        shortcut = _consume_shortcut()
        if shortcut:
            return shortcut
    return console.getwch()


def _consume_shortcut() -> str:
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        signature = [wintypes.HANDLE, ctypes.POINTER(_InputRecord), wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD)]
        kernel.PeekConsoleInputW.argtypes = signature
        kernel.ReadConsoleInputW.argtypes = signature
        handle = kernel.GetStdHandle(-10)
        records, count = (_InputRecord * 32)(), wintypes.DWORD()
        if not kernel.PeekConsoleInputW(handle, records, 32, ctypes.byref(count)):
            return ""
        consumed, shortcut = _shortcut_prefix(records[:count.value])
        if not consumed:
            return ""
        if kernel.ReadConsoleInputW(handle, records, consumed, ctypes.byref(count)):
            return shortcut
        return ""
    except (AttributeError, OSError):
        return ""


def _alt_v_prefix(records: list[_InputRecord]) -> int:
    count, shortcut = _shortcut_prefix(records)
    return count if shortcut == "alt+v" else 0


def _shortcut_prefix(records: list[_InputRecord]) -> tuple[int, str]:
    for index, record in enumerate(records):
        key = record.data.key
        if record.kind != 1 or not key.down:
            continue
        if key.virtual_key in {0x10, 0x11, 0x12}:  # Bare Shift, Ctrl, Alt.
            continue
        if key.virtual_key == 0x56 and key.modifiers & 3 and not key.modifiers & 12:
            return index + 1, "alt+v"
        if key.virtual_key == 0x0D:
            return index + 1, "shift+enter" if key.modifiers & 0x1C else "enter"
        return 0, ""
    return 0, ""
