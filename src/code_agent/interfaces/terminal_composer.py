"""Responsive composers for the Muted Slate terminal."""
from __future__ import annotations

from .terminal_display import clip_display, display_width, safe_text
from .terminal_style import BODY_WHITE, BRAND_CYAN, DIM_GRAY, ColorMode, colorize, color_enabled
from .terminal_tail_content import _render_draft
from .terminal_tail_geometry import (
    LiveTailFrame, LiveTailGeometry, _layout_input, _visible_input_rows, _rewrite_tail,
)
from .terminal_theme import Theme, design_for, recolor, ACTIVE_GOLD


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
    activity_height = 2 if active and height >= 7 else 0
    rows, cursor_row = _visible_input_rows(rows, cursor_row, min(6, height - 3 - activity_height))
    remaining = max(0, height - len(rows) - 3 - activity_height)
    items = palette[:min(14, remaining)]
    remaining -= len(items)
    draft_rows = _render_draft(draft, frame_width, max(0, remaining - 1), color, modern=True)
    lines = _palette_rows(items, frame_width, color) + draft_rows
    if activity_height:
        lines += [_status_row(status_icon, status, "", frame_width, color, status_color),
                  activity_rail(frame_width, motion_progress, color)]
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
    label = f" {'FOLLOW-UP' if active else 'CHAOS AGENT'} "
    hint = " Enter queue · Tab steer · Esc pause " if active else " Enter send · Ctrl+J newline · : commands "
    top = _rule(width, label, design.corners[:2], design.border, ACTIVE_GOLD if active else design.accent, color, progress, exiting)
    bottom = _rule(width, hint, design.corners[2:], design.border, design.muted, color, 1.0, False)
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
    if color_enabled(color):
        background = "\x1b[48;2;23;48;46m"
        body = [background + row.replace("\x1b[0m", "\x1b[0m" + background) + "\x1b[0m" for row in body]
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


def activity_rail(width: int, phase: float, color: ColorMode) -> str:
    """An indeterminate light sweep; never represents task completion percent."""
    center = round(max(0.0, min(1.0, phase)) * (width - 1))
    radius = max(2, round(width * .19))
    shades = ("38;2;67;61;53", "38;2;126;105;76", ACTIVE_GOLD)
    cells = []
    for column in range(width):
        strength = max(0.0, 1 - abs(column - center) / radius)
        code = shades[min(2, int(strength * 3))] if strength else "38;2;34;48;70"
        cells.append(colorize("─", code, color))
    return "".join(cells)
