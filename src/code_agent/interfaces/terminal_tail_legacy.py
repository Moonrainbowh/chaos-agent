from __future__ import annotations

from .terminal_display import clip_display, display_width, safe_text
from .terminal_style import BORDER_GRAY, BRAND_CYAN, BRIGHT_CYAN, DIM_GRAY, BODY_WHITE, SUCCESS_GREEN, WARNING_YELLOW, ColorMode, colorize
from .terminal_tail_geometry import LiveTailFrame, LiveTailGeometry, _layout_input, _visible_input_rows, _rewrite_tail
from .terminal_tail_content import _style_box_border, _style_box_row, _render_palette, _render_draft


from .terminal_legacy_status import _render_modern_bottom_bar


def _make_tagged_top_border(width: int) -> str:
    left = "╭── Prompt "
    right = " [Ctrl+C to Pause] ─╮"
    fill_len = max(0, width - display_width(left) - display_width(right))
    return left + "─" * fill_len + right


def _make_tagged_bottom_border(width: int) -> str:
    left = "╰"
    right = " Enter to Send · Shift+Enter for Newline ─╯"
    fill_len = max(0, width - display_width(left) - display_width(right))
    return left + "─" * fill_len + right


def _normal_frame(
    input_text: str, status: str, width: int, height: int,
    cursor_index: int | None, draft: str, color: ColorMode,
    palette: tuple[str, ...], status_icon: str, status_color: str | None,
    status_context: str, previous: LiveTailGeometry | None,
    theme: object = "symbol",
) -> LiveTailFrame:
    frame_width = max(7, width - 1)
    text_width = max(1, frame_width - 6)
    supplied = safe_text(input_text)
    index = len(supplied) if cursor_index is None else min(max(0, cursor_index), len(supplied))
    rows, cursor_row, cursor_column = _layout_input(supplied, text_width, index)
    placeholder = "Type a task, edit request, or / for commands..."
    placeholder_visible = not supplied
    if placeholder_visible:
        rows = [clip_display(placeholder, text_width)]

    modern = str(theme) in {"modern", "Theme.MODERN"}
    border_code = BORDER_GRAY
    prompt_code = BRAND_CYAN
    top_border = "╭" + "─" * (frame_width - 2) + "╮"
    bottom_border = "╰" + "─" * (frame_width - 2) + "╯"

    return _legacy_frame(rows, cursor_row, cursor_column, frame_width, text_width, height, width, palette, draft, color, status_icon, status, status_context, status_color, previous, placeholder_visible, border_code, prompt_code, bottom_border, modern, top_border)


def _legacy_frame(rows, cursor_row, cursor_column, frame_width, text_width, height, width, palette, draft, color, status_icon, status, status_context, status_color, previous, placeholder_visible, border_code, prompt_code, bottom_border, modern, top_border) -> LiveTailFrame:
    if modern and frame_width >= 40:
        return _modern_frame(rows, cursor_row, cursor_column, frame_width, text_width, height, width, palette, draft, color, status_icon, status, status_context, status_color, previous, placeholder_visible, border_code, prompt_code, bottom_border)

    input_budget = min(len(rows), height - 3)
    rows, cursor_row = _visible_input_rows(rows, cursor_row, input_budget)
    remaining = height - len(rows) - 3
    palette_items = tuple(safe_text(item).replace("\n", " ") for item in palette)[
        : min(14, remaining)
    ]
    remaining -= len(palette_items)
    draft_lines = _render_draft(draft, width, max(0, remaining - 1), color, modern=modern)
    lines = _render_palette(palette_items, width, color) + draft_lines
    lines.append(_style_box_border(top_border, color, border_code=border_code))
    for row_index, row in enumerate(rows):
        prompt = "› " if row_index == 0 else "  "
        padding = " " * max(0, text_width - display_width(row))
        lines.append(
            _style_box_row(
                prompt,
                row,
                padding,
                color,
                placeholder=placeholder_visible,
                border_code=border_code,
                prompt_code=prompt_code,
            )
        )
    lines.append(_style_box_border(bottom_border, color, border_code=border_code))

    lines.append(_render_status(status_icon, status, status_context, width, color, status_color))

    geometry = LiveTailGeometry(height=len(lines), cursor_row=cursor_row + 1 + len(palette_items) + len(draft_lines))
    output = _rewrite_tail(
        lines, geometry.cursor_row, cursor_column + 4, previous, height
    )
    return LiveTailFrame(output, geometry)




def _modern_frame(rows, cursor_row, cursor_column, frame_width, text_width, height, width, palette, draft, color, status_icon, status, status_context, status_color, previous, placeholder_visible, border_code, prompt_code, bottom_border) -> LiveTailFrame:
    input_budget = min(len(rows), max(1, height - 4))
    rows, cursor_row = _visible_input_rows(rows, cursor_row, input_budget)
    remaining = height - len(rows) - 4
    palette_items = tuple(safe_text(item).replace("\n", " ") for item in palette)[
        : min(14, remaining)
    ]
    remaining -= len(palette_items)
    draft_lines = _render_draft(draft, width, max(0, remaining - 1), color, modern=True)
    lines = _render_palette(palette_items, width, color) + draft_lines

    lines.append(_style_box_border(_make_tagged_top_border(frame_width), color, border_code=border_code))
    for row_index, row in enumerate(rows):
        prompt = "❯ " if row_index == 0 else "  "
        padding = " " * max(0, text_width - display_width(row))
        lines.append(
            _style_box_row(
                prompt,
                row,
                padding,
                color,
                placeholder=placeholder_visible,
                border_code=border_code,
                prompt_code=prompt_code,
            )
        )
    mid_border = "├" + "─" * (frame_width - 2) + "┤"
    lines.append(_style_box_border(mid_border, color, border_code=border_code))
    lines.append(
        _render_modern_bottom_bar(
            status_icon, status, status_context, frame_width, color, status_color,
            border_code=border_code,
        )
    )
    lines.append(_style_box_border(bottom_border, color, border_code=border_code))

    geometry = LiveTailGeometry(
        height=len(lines),
        cursor_row=cursor_row + 1 + len(palette_items) + len(draft_lines),
    )
    output = _rewrite_tail(
        lines, geometry.cursor_row, cursor_column + 4, previous, height
    )
    return LiveTailFrame(output, geometry)


def _render_status(
    icon: str, status: str, context: str, width: int, color: ColorMode, status_color: str | None
) -> str:
    left = clip_display(safe_text(icon) + " " + safe_text(status).replace("\n", " "), width)
    right = safe_text(context).replace("\n", " ")
    if right and display_width(left) + display_width(right) + 2 <= width:
        padding = " " * (width - display_width(left) - display_width(right))
        return colorize(left, status_color or DIM_GRAY, color) + padding + colorize(right, DIM_GRAY, color)
    return colorize(left, status_color or DIM_GRAY, color)


