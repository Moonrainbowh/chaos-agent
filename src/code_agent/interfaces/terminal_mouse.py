"""Shared SGR mouse decoding for native Windows and SSH/POSIX terminals."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MouseClick:
    """One left-button press in one-based terminal viewport coordinates."""
    column: int
    row: int


_SGR = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])$")


def mouse_key(sequence: str) -> str | MouseClick:
    match = _SGR.fullmatch(sequence)
    if match is None or match[4] != "M":
        return ""
    button, column, row = (int(match[index]) for index in (1, 2, 3))
    if column < 1 or row < 1:
        return ""
    if button & 64:
        return "scroll_up" if button & 1 == 0 else "scroll_down"
    return MouseClick(column, row) if button == 0 else ""
