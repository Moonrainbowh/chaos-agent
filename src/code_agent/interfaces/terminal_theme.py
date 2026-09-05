"""Trusted terminal design tokens; never interpret model text as styling."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .terminal_style import BODY_WHITE, BORDER_GRAY, BRAND_CYAN, BRIGHT_CYAN, DIM_GRAY, TOOL_GRAY


class Theme(str, Enum):
    SLATE = "slate"
    SIGNAL = "signal"
    SYMBOL = "symbol"
    PLAIN = "plain"
    MODERN = "modern"
    AURORA = "aurora"
    EMBER = "ember"
    MONO = "mono"


@dataclass(frozen=True)
class TerminalDesign:
    name: str
    description: str
    accent: str
    body: str
    muted: str
    border: str
    corners: str
    marker: str
    frames: str


DESIGNS = {
    Theme.SLATE: TerminalDesign(
        "CHAOS", "Muted Slate · Cold brew", "38;2;125;211;252", "38;2;203;213;225",
        "38;2;115;132;156", "38;2;34;48;70", "╭╮╰╯", "›", "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
    ),
}
ACTIVE_GOLD = "38;2;226;177;112"
_SGR = re.compile(r"\x1b\[([0-9;]+)m")


def design_for(theme: object) -> TerminalDesign | None:
    """Return a design for opt-in themes, preserving legacy output byte for byte."""
    return DESIGNS.get(theme)


def preferred_theme(env: dict[str, str] | None = None) -> Theme:
    """Return the single product theme; ignore retired environment preferences."""
    return Theme.SLATE


def recolor(rendered: str, theme: object) -> str:
    """Map only SGR tokens produced by trusted renderers; preserve status semantics."""
    design = design_for(theme)
    if design is None:
        return rendered
    mapping = {
        BRAND_CYAN: design.accent, BRIGHT_CYAN: "1;" + design.accent,
        BODY_WHITE: design.body, DIM_GRAY: design.muted,
        TOOL_GRAY: design.muted, BORDER_GRAY: design.border,
        "38;5;179": ACTIVE_GOLD, "38;5;203": "38;2;248;113;113", "38;5;115": "38;2;110;231;183",
    }
    return _SGR.sub(lambda match: f"\x1b[{mapping.get(match[1], match[1])}m", rendered)
