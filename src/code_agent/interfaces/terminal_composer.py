"""Responsive composers for the three terminal designs."""
from __future__ import annotations

from .terminal_display import clip_display, display_width, safe_text
from .terminal_style import BODY_WHITE, BRAND_CYAN, DIM_GRAY, ColorMode, colorize
from .terminal_tail_content import _render_draft
from .terminal_tail_geometry import (
    LiveTailFrame, LiveTailGeometry, _layout_input, _visible_input_rows, _rewrite_tail,
)
from .terminal_theme import Theme, design_for, recolor


def render_designed_frame(
    input_text: str, status: str, width: int, height: int,
    cursor_index: int | None, draft: str, color: ColorMode,
    palette: tuple[str, ...], status_icon: str, status_color: str | None,
    context: str, previous: LiveTailGeometry | None, theme: Theme,
    motion_progress: float, exiting: bool, active: bool,
) -> LiveTailFrame:
    """Keep the cursor visible and every physical row within the terminal budget."""
    frame_width = min(110, width - 1)
    text_width = max(1, frame_width - 6)
    supplied = safe_text(input_text)
    index = len(supplied) if cursor_index is None else min(max(0, cursor_index), len(supplied))
    rows, cursor_row, cursor_col = _layout_input(supplied, text_width, index)
    if not supplied:
        placeholder = "Describe your next step..." if active else "What would you like to build?"
        rows = [clip_display(placeholder, text_width)]
    rows, cursor_row = _visible_input_rows(rows, cursor_row, min(6, height - 3))
    remaining = max(0, height - len(rows) - 3)
    items = palette[:min(14, remaining)]
    remaining -= len(items)
    draft_rows = _render_draft(draft, frame_width, max(0, remaining - 1), color, modern=True)
    lines = _palette_rows(items, frame_width, color) + draft_rows
    offset = len(lines)
    lines += composer_rows(rows, frame_width, theme, color, not supplied, active, motion_progress, exiting)
    lines.append(_status_row(status_icon, status, context, frame_width, color, status_color))
    geometry = LiveTailGeometry(len(lines), offset + cursor_row + 1)
    rendered = _rewrite_tail(lines, geometry.cursor_row, cursor_col + 4, previous, height)
    return LiveTailFrame(recolor(rendered, theme), geometry)


def composer_rows(
    rows: list[str], width: int, theme: Theme, color: ColorMode,
    placeholder: bool, active: bool, progress: float, exiting: bool,
) -> list[str]:
    design = design_for(theme)
    label = f" {design.name} / {'FOLLOW-UP' if active else 'COMPOSE'} "
    hint = " Enter queue · Tab steer · Esc pause " if active else " Enter send · Ctrl+J newline · : commands "
    top = _rule(width, label, design.corners[:2], design.border, design.accent, color, progress, exiting)
    bottom = _rule(width, hint, design.corners[2:], design.border, design.border, color, 1.0, False)
    side = " " if theme is Theme.MONO else "│"
    body = []
    for number, row in enumerate(rows):
        prompt = design.marker if number == 0 else "·"
        value = row + " " * max(0, width - 6 - display_width(row))
        body.append(
            colorize(side + " ", design.border, color)
            + colorize(prompt + " ", design.accent, color)
            + colorize(value, design.muted if placeholder else design.body, color)
            + colorize(" " + side, design.border, color)
        )
    return [top, *body, bottom]


def _rule(width, label, corners, base, accent, color, progress, exiting):
    # Hide optional hints before clipping a tiny terminal. Geometry stays fixed.
    if display_width(label) + 6 > width:
        label = ""
    interior = "─" + label + "─" * max(0, width - display_width(label) - 3)
    fraction = 1 - progress if exiting else progress
    visible = min(len(interior), max(0, round(len(interior) * fraction)))
    return (
        colorize(corners[0], base, color)
        + colorize(interior[:visible], accent, color)
        + colorize(interior[visible:], base, color)
        + colorize(corners[1], base, color)
    )


def _status_row(icon, status, context, width, color, status_color):
    left = clip_display(safe_text(f"{icon} {status}").replace("\n", " "), width)
    parts = [part.strip() for part in safe_text(context).replace("\n", " ").split(" · ") if part.strip()]
    # Retain the model first; metadata disappears as a whole before the status.
    right = " · ".join(parts)
    while parts and display_width(left) + display_width(right) + 3 > width:
        parts.pop()
        right = " · ".join(parts)
    gap = " " * max(0, width - display_width(left) - display_width(right))
    return colorize(left, status_color or DIM_GRAY, color) + gap + colorize(right, DIM_GRAY, color)


def _palette_rows(items, width, color):
    return [
        colorize(clip_display(safe_text(item).replace("\n", " "), width),
                 BRAND_CYAN if "› " in item[:5] else DIM_GRAY, color)
        for item in items
    ]
