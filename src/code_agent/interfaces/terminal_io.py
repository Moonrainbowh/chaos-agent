from __future__ import annotations

import sys

from .terminal_renderer import ColorMode, Theme, render_entries, render_live_tail
from .terminal_state import TerminalState


def read_key() -> str:
    import msvcrt

    key = msvcrt.getwch()
    if key not in {"\x00", "\xe0"}:
        return key
    return {"K": "left", "M": "right", "G": "home", "O": "end", "H": "up", "P": "down", "S": "delete"}.get(msvcrt.getwch(), "")


def stdout_write(value: str) -> None:
    sys.stdout.write(value)
    sys.stdout.flush()


def render_terminal(state: TerminalState, input_text: str, columns: int, rows: int, **_: object) -> str:
    """Compatibility helper for tests; it never clears or replaces terminal history."""
    _ = rows
    return render_entries(state.entries, columns, theme=Theme.SYMBOL, color=ColorMode.NEVER) + "\n" + render_live_tail(input_text, state.status, columns, color=ColorMode.NEVER)
