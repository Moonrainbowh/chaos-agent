from __future__ import annotations

from .terminal_composer import _status_row
from .terminal_style import BORDER_GRAY, ColorMode, colorize


def _render_modern_bottom_bar(
    status_icon: str, status: str, context: str, frame_width: int,
    color: ColorMode, status_color: str | None, *, border_code: str = BORDER_GRAY,
) -> str:
    """Show supplied facts, with no invented progress blocks or inferred usage."""
    content = _status_row(status_icon, status, context, max(1, frame_width - 4), color, status_color)
    return colorize("│ ", border_code, color) + content + colorize(" │", border_code, color)
