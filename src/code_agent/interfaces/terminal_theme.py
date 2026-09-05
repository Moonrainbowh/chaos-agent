"""Trusted terminal design tokens; never interpret model text as styling."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum

from .terminal_style import BODY_WHITE, BORDER_GRAY, BRAND_CYAN, BRIGHT_CYAN, DIM_GRAY, TOOL_GRAY


class Theme(str, Enum):
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
    Theme.AURORA: TerminalDesign(
        "AURORA", "Cyan · Rounded workspace", "38;5;117", "38;5;255", "38;5;250",
        "38;5;103", "╭╮╰╯", "›", "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
    ),
    Theme.EMBER: TerminalDesign(
        "EMBER", "Gold · Precision console", "38;5;222", "38;5;230", "38;5;250",
        "38;5;137", "┌┐└┘", "›", "◴◷◶◵",
    ),
    Theme.MONO: TerminalDesign(
        "MONO", "Monochrome · Focused writing", "38;5;255", "38;5;254", "38;5;250",
        "38;5;245", "    ", "›", "·∙●∙",
    ),
}
_SGR = re.compile(r"\x1b\[([0-9;]+)m")


def design_for(theme: object) -> TerminalDesign | None:
    """Return a design for opt-in themes, preserving legacy output byte for byte."""
    return DESIGNS.get(theme)


def preferred_theme(env: dict[str, str] | None = None) -> Theme:
    """Read the local process preference; invalid values fall back to Aurora."""
    source = os.environ if env is None else env
    try:
        return Theme(source.get("CHAOS_THEME", "aurora").casefold())
    except ValueError:
        return Theme.AURORA


def recolor(rendered: str, theme: object) -> str:
    """Map only SGR tokens produced by trusted renderers; preserve status semantics."""
    design = design_for(theme)
    if design is None:
        return rendered
    mapping = {
        BRAND_CYAN: design.accent, BRIGHT_CYAN: "1;" + design.accent,
        BODY_WHITE: design.body, DIM_GRAY: design.muted,
        TOOL_GRAY: design.muted, BORDER_GRAY: design.border,
    }
    return _SGR.sub(lambda match: f"\x1b[{mapping.get(match[1], match[1])}m", rendered)
