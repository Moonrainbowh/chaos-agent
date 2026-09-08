from __future__ import annotations

import sys
import time
from collections import deque
from collections.abc import Callable
import os

from .input_events import MAX_PASTE_BYTES
from .console_shortcuts import read_character

from .terminal_renderer import ColorMode, Theme, render_entries, render_live_tail
from .terminal_state import TerminalState


BRACKETED_PASTE_ENABLE = "\x1b[?2004h"
BRACKETED_PASTE_DISABLE = "\x1b[?2004l"
_EXTENDED_KEYS = {
    "K": "left",
    "M": "right",
    "G": "home",
    "O": "end",
    "H": "up",
    "P": "down",
    "S": "delete",
    "I": "page_up",
    "Q": "page_down",
}


_pending: deque[str] = deque()
_console = None
_PASTE_START = "\x1b[200~"
_PASTE_END = "\x1b[201~"


def capture_ctrl_c_as_input() -> Callable[[], None]:
    """Temporarily deliver Ctrl+C as \x03 instead of a process signal."""
    if os.name != "nt":
        return _noop
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle, original = kernel.GetStdHandle(-10), wintypes.DWORD()
        if not kernel.GetConsoleMode(handle, original):
            return _noop
        if not kernel.SetConsoleMode(handle, original.value & ~0x0001):
            return _noop
    except (AttributeError, OSError):
        return _noop

    def restore() -> None:
        try:
            kernel.SetConsoleMode(handle, original.value)
        except OSError:
            pass

    return restore


def _noop() -> None:
    return None


def _enter_sequence(sequence: str) -> str:
    """Normalize Kitty, modifyOtherKeys, and Windows VT Return encodings."""
    if sequence.endswith("u") and sequence.startswith("\x1b["):
        fields = sequence[2:-1].split(";")
        key_code = fields[0].split(":", 1)[0]
        if key_code != "13":
            return ""
        modifier = fields[1].split(":", 1)[0] if len(fields) > 1 else "1"
        flags = max(0, int(modifier) - 1) if modifier.isdecimal() else 0
        return "shift+enter" if flags & 5 else "\r"
    if sequence.endswith("~") and sequence.startswith("\x1b[27;"):
        fields = sequence[2:-1].split(";")
        if len(fields) == 3 and fields[2] == "13":
            flags = max(0, int(fields[1]) - 1) if fields[1].isdecimal() else 0
            return "shift+enter" if flags & 5 else "\r"
    if sequence.endswith("_") and sequence.startswith("\x1b["):
        fields = sequence[2:-1].split(";")
        if len(fields) >= 5 and fields[0] == "13" and fields[3] == "1":
            control = int(fields[4]) if fields[4].isdecimal() else 0
            return "shift+enter" if control & 28 else "\r"
    return ""


def _shift_is_pressed() -> bool:
    """Read Shift or Ctrl synchronously while a carriage-return key event is handled."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        shift = bool(user32.GetKeyState(0x10) & 0x8000)  # VK_SHIFT
        ctrl = bool(user32.GetKeyState(0x11) & 0x8000)   # VK_CONTROL
        return shift or ctrl
    except (AttributeError, OSError):
        return False


def _available(console, timeout=.02):
    deadline = time.monotonic() + timeout
    while not console.kbhit():
        if time.monotonic() >= deadline:
            return False
        time.sleep(.001)
    return True


def read_key(*, timeout: float | None = None) -> str | None:
    """Keep framed paste atomic even when the console delivers its marker in chunks."""
    import msvcrt
    global _console
    if _console is not msvcrt:
        _pending.clear()
        _console = msvcrt
    if not _pending and timeout is not None and not _available(msvcrt, timeout):
        return None
    key = _pending.popleft() if _pending else read_character(msvcrt)
    if key == "enter":
        return "\r"
    if key in {"alt+v", "shift+enter"}:
        return key
    if key == "\x1b":
        if not _available(msvcrt, .08):
            return key
        suffix = msvcrt.getwch()
        if suffix in {"v", "V"}:
            return "alt+v"
        if suffix != "[":
            _pending.append(suffix)
            return key
        # Once CSI begins, read its terminator rather than treating a partial
        # marker as Escape followed by ordinary text/Enter events.
        sequence = "\x1b["
        while len(sequence) < 32:
            character = msvcrt.getwch()
            sequence += character
            if "@" <= character <= "~":
                break
        if sequence == _PASTE_START:
            return _read_bracketed_paste(msvcrt)
        return _enter_sequence(sequence)
    if key in {"\x00", "\xe0"}:
        suffix = msvcrt.getwch()
        if suffix == "/":  # Windows console Alt+V scan code (VK_V -> 0x2f).
            return "alt+v"
        if suffix == "\r" and _shift_is_pressed():
            return "shift+enter"
        return _EXTENDED_KEYS.get(suffix, "")
    if key == "\r" and _shift_is_pressed():
        return "shift+enter"
    if key.isprintable():
        return _read_text_burst(msvcrt, key)
    return key


def _read_bracketed_paste(console: object) -> str:
    """Drain oversized pastes to the end marker while bounding retained memory."""
    chars, suffix = [], ""
    overflow = False
    while True:
        suffix += console.getwch()
        if suffix == _PASTE_END:
            value = "x" * (MAX_PASTE_BYTES + 1) if overflow else "".join(chars)
            return _PASTE_START + value + _PASTE_END
        while suffix and not _PASTE_END.startswith(suffix):
            if len(chars) < MAX_PASTE_BYTES + 1:
                chars.append(suffix[0])
            else:
                overflow = True
            suffix = suffix[1:]


def _read_text_burst(console, first):
    """Coalesce legacy console paste bursts so embedded CR/LF cannot submit."""
    chars, count = [first], 1
    while _available(console):
        character = read_character(console)
        if character in {"alt+v", "enter", "shift+enter"} or (not character.isprintable() and character not in {"\r", "\n", "\t"}):
            _pending.append(character)
            break
        count += 1
        if len(chars) <= MAX_PASTE_BYTES:
            chars.append(character)
    if count == 1:
        return first
    value = "x" * (MAX_PASTE_BYTES + 1) if count > len(chars) else "".join(chars)
    return _PASTE_START + value + _PASTE_END


def stdout_write(value: str) -> None:
    try:
        sys.stdout.write(value)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(value.encode("utf-8", errors="replace"))
    sys.stdout.flush()


def render_terminal(state: TerminalState, input_text: str, columns: int, rows: int, **_: object) -> str:
    """Compatibility helper for tests; it never clears or replaces terminal history."""
    _ = rows
    return render_entries(state.entries, columns, theme=Theme.SYMBOL, color=ColorMode.NEVER) + "\n" + render_live_tail(input_text, state.status, columns, color=ColorMode.NEVER)
