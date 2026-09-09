"""Responsive composers for the Muted Slate terminal."""
from __future__ import annotations

from .terminal_display import clip_display, display_width, safe_text
from .terminal_style import BODY_WHITE, BRAND_CYAN, DIM_GRAY, ColorMode, colorize, color_enabled
from .terminal_tail_content import _render_draft
from .terminal_tail_geometry import (
    LiveTailFrame, LiveTailGeometry, _layout_input, _visible_input_rows, _rewrite_tail, tail_geometry,
)
from .terminal_theme import Theme, design_for, recolor, ACTIVE_GOLD


def render_designed_frame(
    input_text: str, status: str, width: int, height: int,
    cursor_index: int | None, draft: str, color: ColorMode,
    palette: tuple[str, ...], status_icon: str, status_color: str | None,
    context: str, previous: LiveTailGeometry | None, theme: Theme,
    motion_progress: float, exiting: bool, active: bool,
    *,
    expanded: bool = True,
    image_count: int = 0,
) -> LiveTailFrame:
    """Keep the cursor visible and every physical row within the terminal budget."""
    safe_supplied = safe_text(input_text)
    if not expanded and not palette and not draft and not safe_supplied.strip():
        return render_collapsed_capsule(
            safe_supplied, status, width, height, color, status_icon,
            status_color, context, previous, theme, active=active,
        )

    frame_width = max(1, width - 2)
    text_width = max(1, frame_width - 6)
    supplied = safe_supplied
    index = len(supplied) if cursor_index is None else min(max(0, cursor_index), len(supplied))
    rows, cursor_row, cursor_col = _layout_input(supplied, text_width, index)
    if not supplied:
        placeholder = "Describe your next step..." if active else "What would you like to build?"
        rows = [clip_display(placeholder, text_width)]
    # Target 6 lines for comfortable multi-line editing, clamped by terminal height
    target_rows = max(1, min(6, height - 3))
    rows, cursor_row = _visible_input_rows(rows, cursor_row, target_rows)
    input_box_rows = len(rows) if (palette or draft) else target_rows
    remaining = max(0, height - input_box_rows - 3)
    items = palette[:min(14, remaining)]
    remaining -= len(items)
    draft_rows = _render_draft(draft, frame_width, max(0, remaining - 1), color, modern=True)
    lines = _palette_rows(items, frame_width, color) + draft_rows
    offset = len(lines)
    lines += composer_rows(
        rows, frame_width, theme, color, not supplied, active, motion_progress, exiting,
        char_count=len(supplied), image_count=image_count, target_rows=input_box_rows,
    )
    lines.append(_status_row(status_icon, status, context, frame_width, color, status_color))
    geometry = tail_geometry(lines, offset + cursor_row + 1, cursor_col + 4)
    rendered = _rewrite_tail(lines, geometry.cursor_row, cursor_col + 4, previous, height)
    return LiveTailFrame(recolor(rendered, theme), geometry)


def render_collapsed_capsule(
    input_text: str, status: str, width: int, height: int,
    color: ColorMode, status_icon: str, status_color: str | None,
    context: str, previous: LiveTailGeometry | None, theme: Theme,
    *,
    active: bool = False,
) -> LiveTailFrame:
    frame_width = max(1, width - 2)
    design = design_for(theme)
    supplied = input_text.strip()
    if supplied:
        left = f"› [Draft: {clip_display(supplied.replace(chr(10), ' '), 28)}] · [Space] Edit"
    else:
        left = "› [Space] Compose · [:] Commands" if not active else "› [Space] Steer/Queue · [Esc] Pause"
    left_width = display_width(left)
    right_raw = f"{status_icon} {status} · {context}".strip(" · ")
    max_right = max(1, frame_width - left_width - 6)
    right = clip_display(right_raw, max_right)
    gap = " " * max(1, frame_width - 4 - left_width - display_width(right))
    left_part = colorize(left, design.accent if supplied else design.muted, color)
    right_part = colorize(right, status_color or design.muted, color)
    capsule = colorize("╭─ ", design.border, color) + left_part + gap + right_part + colorize(" ─╮", design.border, color)
    lines = [capsule]
    geometry = tail_geometry(lines, 0, 4)
    rendered = _rewrite_tail(lines, 0, 4, previous, height)
    return LiveTailFrame(recolor(rendered, theme), geometry)


def composer_rows(
    rows: list[str], width: int, theme: Theme, color: ColorMode,
    placeholder: bool, active: bool, progress: float, exiting: bool,
    *,
    char_count: int = 0,
    image_count: int = 0,
    target_rows: int = 1,
) -> list[str]:
    design = design_for(theme)
    label = f" {'FOLLOW-UP' if active else 'CHAOS AGENT'} "
    hint = " Esc collapse · Enter queue · Tab steer " if active else " Esc collapse · Enter send · Shift+Enter newline "
    top = _rule(width, label, design.corners[:2], design.border, design.accent, color,
                0.0 if active else progress, False if active else exiting)
    if active:
        top = top.replace(label, colorize(label, design.muted, color) + ("\x1b[" + design.border + "m" if color_enabled(color) else ""))
    left_meta = ""
    if char_count > 0:
        left_meta = f" {char_count} chars "
        if image_count > 0:
            left_meta += f"· 📎 {image_count} "
    bottom = _bottom_rule(width, left_meta, hint, design.corners[2:], design.border, design.muted, color)
    side = " " if theme is Theme.MONO else "│"
    body = []
    rendered_rows = list(rows)
    while len(rendered_rows) < target_rows:
        rendered_rows.append("")
    for number, row in enumerate(rendered_rows):
        prompt = design.marker if number == 0 else (" " if active else "·")
        prompt_accent = design.accent
        value = row + " " * max(0, width - 6 - display_width(row))
        body.append(
            colorize(side + " ", design.border, color)
            + colorize(prompt + " ", prompt_accent, color)
            + colorize(value, design.muted if (placeholder and number == 0) else design.body, color)
            + colorize(" " + side, design.border, color)
        )
    if color_enabled(color):
        background = "\x1b[48;2;23;48;46m"
        body = [background + row.replace("\x1b[0m", "\x1b[0m" + background) + "\x1b[0m" for row in body]
    return [top, *body, bottom]


def _bottom_rule(width: int, left_meta: str, right_hint: str, corners: str, base: str, text_color: str, color: ColorMode) -> str:
    if display_width(left_meta) + display_width(right_hint) + 6 > width:
        left_meta = ""
    if display_width(right_hint) + 6 > width:
        right_hint = ""
    dash_len = max(0, width - display_width(left_meta) - display_width(right_hint) - 2)
    interior = left_meta + "─" * dash_len + right_hint
    return (
        colorize(corners[0], base, color)
        + colorize(interior, base, color)
        + colorize(corners[1], base, color)
    )


def _rule(width, label, corners, base, accent, color, progress, exiting, *, align_right: bool = False):
    # Hide optional hints before clipping a tiny terminal. Geometry stays fixed.
    if display_width(label) + 6 > width:
        label = ""
    if align_right and label:
        interior = "─" * max(0, width - display_width(label) - 3) + label + "─"
    else:
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
