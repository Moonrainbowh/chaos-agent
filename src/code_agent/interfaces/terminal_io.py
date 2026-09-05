from __future__ import annotations

import sys
import time
from collections import deque

from .input_events import MAX_PASTE_BYTES

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


def _available(console, timeout=.02):
    deadline = time.monotonic() + timeout
    while not console.kbhit():
        if time.monotonic() >= deadline:
            return False
        time.sleep(.001)
    return True


def read_key() -> str:
    """Keep framed paste atomic even when the console delivers its marker in chunks."""
    import msvcrt
    global _console
    if _console is not msvcrt:
        _pending.clear()
        _console = msvcrt
    key = _pending.popleft() if _pending else msvcrt.getwch()
    if key == "\x1b":
        if not _available(msvcrt, .08):
            return key
        suffix = msvcrt.getwch()
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
        return _read_bracketed_paste(msvcrt) if sequence == _PASTE_START else ""
    if key in {"\x00", "\xe0"}:
        return _EXTENDED_KEYS.get(msvcrt.getwch(), "")
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
        character = console.getwch()
        if not character.isprintable() and character not in {"\r", "\n", "\t"}:
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
