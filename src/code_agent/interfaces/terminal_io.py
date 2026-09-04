from __future__ import annotations

import sys

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


def read_key() -> str:
    import msvcrt

    key = msvcrt.getwch()
    if key == "\x1b" and msvcrt.kbhit():
        suffix = msvcrt.getwch()
        if suffix == "[" and msvcrt.kbhit() and msvcrt.getwch() == "2" and msvcrt.kbhit() and msvcrt.getwch() == "0" and msvcrt.kbhit() and msvcrt.getwch() == "0" and msvcrt.kbhit() and msvcrt.getwch() == "~":
            return _read_bracketed_paste(msvcrt)
        return "\x1b"
    if key not in {"\x00", "\xe0"}:
        return key
    return _EXTENDED_KEYS.get(msvcrt.getwch(), "")


def _read_bracketed_paste(msvcrt: object) -> str:
    chars: list[str] = []
    while True:
        chars.append(msvcrt.getwch())
        if len(chars) >= 6 and "".join(chars[-6:]) == "\x1b[201~":
            return "\x1b[200~" + "".join(chars)


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
