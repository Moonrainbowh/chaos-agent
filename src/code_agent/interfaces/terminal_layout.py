"""Terminal layout choices and tail-relative touch geometry."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LayoutMode(str, Enum):
    AUTO = "auto"
    WIDE = "wide"
    COMPACT = "compact"

    def resolve(self, columns: int) -> LayoutMode:
        """Use actual terminal columns, before any content margins are removed."""
        if self is LayoutMode.AUTO:
            return LayoutMode.COMPACT if columns <= 64 else LayoutMode.WIDE
        return self


@dataclass(frozen=True)
class TouchRegion:
    """Half-open, zero-based coordinates relative to the rendered tail."""

    action: str
    row: int
    left: int
    right: int

    def contains(self, row: int, column: int) -> bool:
        return self.row == row and self.left <= column < self.right
