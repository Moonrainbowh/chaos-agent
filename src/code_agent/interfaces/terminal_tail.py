from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .terminal_display import clip_display, display_width, safe_text
from .terminal_style import BORDER_GRAY, BRAND_CYAN, DIM_GRAY, BODY_WHITE, ColorMode, colorize


@dataclass(frozen=True)
class LiveTailGeometry:
    height: int
    cursor_row: int


@dataclass(frozen=True)
class LiveTailFrame:
    text: str
    geometry: LiveTailGeometry


def render_live_tail(
    input_text: str,
    status: str,
    width: int,
    *,
    cursor_index: int | None = None,
    assistant_draft: str = "",
    terminal_height: int = 30,
    color: ColorMode = ColorMode.AUTO,
    palette: Iterable[str] = (),
    status_icon: str = ".",
    status_color: str | None = None,
    status_context: str = "",
) -> str:
    """Render a fresh bordered composer and status line without touching scrollback."""
    return render_live_tail_frame(
        input_text,
        status,
        width,
        cursor_index=cursor_index,
        assistant_draft=assistant_draft,
        terminal_height=terminal_height,
        color=color,
        palette=palette,
        status_icon=status_icon,
        status_color=status_color,
        status_context=status_context,
    ).text


def render_live_tail_frame(
    input_text: str,
    status: str,
    width: int,
    *,
    cursor_index: int | None = None,
    assistant_draft: str = "",
    terminal_height: int = 30,
    color: ColorMode = ColorMode.AUTO,
    palette: Iterable[str] = (),
    status_icon: str = ".",
    status_color: str | None = None,
    status_context: str = "",
    previous: LiveTailGeometry | None = None,
) -> LiveTailFrame:
    """Rewrite only the previous dynamic tail and return its new cursor geometry."""
    frame_width = max(8, width - 1)
    text_width = max(1, frame_width - 6)
    supplied = safe_text(input_text)
    index = len(supplied) if cursor_index is None else min(max(0, cursor_index), len(supplied))
    rows, cursor_row, cursor_column = _layout_input(supplied, text_width, index)
    placeholder = "输入任务、编辑请求，或输入 / 查看命令"
    placeholder_visible = not supplied
    if placeholder_visible:
        rows = [clip_display(placeholder, text_width)]

    top_border = "╭" + "─" * (frame_width - 2) + "╮"
    bottom_border = "╰" + "─" * (frame_width - 2) + "╯"
    palette_items = tuple(safe_text(item).replace("\n", " ") for item in palette)[:5]
    draft_budget = max(1, terminal_height - len(palette_items) - len(rows) - 5)
    draft_lines = _render_draft(assistant_draft, width, draft_budget, color)
    lines = draft_lines + _render_palette(palette_items, width, color)
    lines.append(_style_box_border(top_border, color))
    for row_index, row in enumerate(rows):
        prompt = "› " if row_index == 0 else "  "
        padding = " " * max(0, text_width - display_width(row))
        lines.append(_style_box_row(prompt, row, padding, color, placeholder=placeholder_visible))
    lines.append(_style_box_border(bottom_border, color))

    lines.append(_render_status(status_icon, status, status_context, width, color, status_color))

    geometry = LiveTailGeometry(height=len(lines), cursor_row=cursor_row + 1 + len(palette_items) + len(draft_lines))
    output = _rewrite_tail(lines, geometry.cursor_row, cursor_column + 4, previous)
    return LiveTailFrame(output, geometry)


def clear_live_tail(geometry: LiveTailGeometry | None) -> str:
    """Erase a rendered dynamic tail and return the cursor to its top row."""
    if geometry is None:
        return "\r\x1b[2K"
    parts = ["\r"]
    if geometry.cursor_row:
        parts.append(f"\x1b[{geometry.cursor_row}A")
    for row in range(geometry.height):
        parts.append("\x1b[2K")
        if row < geometry.height - 1:
            parts.append("\n\r")
    parts.append("\r")
    if geometry.height > 1:
        parts.append(f"\x1b[{geometry.height - 1}A")
    return "".join(parts)


def _layout_input(value: str, width: int, cursor_index: int) -> tuple[list[str], int, int]:
    rows = [""]
    row_widths = [0]
    cursor_row = 0
    cursor_column = 0
    for index, char in enumerate(value):
        if index == cursor_index:
            cursor_row, cursor_column = len(rows) - 1, row_widths[-1]
        if char == "\n":
            rows.append("")
            row_widths.append(0)
            continue
        char_width = display_width(char)
        if rows[-1] and row_widths[-1] + char_width > width:
            rows.append("")
            row_widths.append(0)
        rows[-1] += char
        row_widths[-1] += char_width
    if cursor_index == len(value):
        cursor_row, cursor_column = len(rows) - 1, row_widths[-1]
    return rows, cursor_row, cursor_column


def _style_box_border(value: str, color: ColorMode) -> str:
    return colorize(value, BORDER_GRAY, color)


def _style_box_row(prompt: str, value: str, padding: str, color: ColorMode, *, placeholder: bool) -> str:
    plain = "│ " + prompt + value + padding + " │"
    body_code = DIM_GRAY if placeholder else BODY_WHITE
    return (
        colorize("│ ", BORDER_GRAY, color)
        + colorize(prompt, BRAND_CYAN, color)
        + colorize(value + padding, body_code, color)
        + colorize(" │", BORDER_GRAY, color)
    )


def _render_palette(items: tuple[str, ...], width: int, color: ColorMode) -> list[str]:
    selected = next((index for index, item in enumerate(items) if item.lstrip().startswith("› ")), 0)
    return [colorize(clip_display("  " + item, width), BRAND_CYAN if index == selected else DIM_GRAY, color) for index, item in enumerate(items)]


def _render_draft(value: str, width: int, max_rows: int, color: ColorMode) -> list[str]:
    if not value:
        return []
    rows = _wrap_plain(safe_text(value), max(1, width - 4))
    clipped = rows[-max_rows:]
    if len(rows) > len(clipped):
        clipped[0] = "… " + clipped[0]
    return [colorize("◆ 正在回答", BRAND_CYAN, color)] + [
        colorize("  " + row, BODY_WHITE, color) for row in clipped
    ]


def _wrap_plain(value: str, width: int) -> list[str]:
    rows: list[str] = []
    for logical in value.split("\n"):
        remaining = logical
        if not remaining:
            rows.append("")
        while remaining:
            part = clip_display(remaining, width) or remaining[0]
            rows.append(part)
            remaining = remaining[len(part):]
    return rows


def _render_status(
    icon: str, status: str, context: str, width: int, color: ColorMode, status_color: str | None
) -> str:
    left = clip_display(safe_text(icon) + " " + safe_text(status).replace("\n", " "), width)
    right = safe_text(context).replace("\n", " ")
    if right and display_width(left) + display_width(right) + 2 <= width:
        padding = " " * (width - display_width(left) - display_width(right))
        return colorize(left, status_color or DIM_GRAY, color) + padding + colorize(right, DIM_GRAY, color)
    return colorize(left, status_color or DIM_GRAY, color)


def _rewrite_tail(lines: list[str], cursor_row: int, cursor_column: int, previous: LiveTailGeometry | None) -> str:
    parts = ["\r"]
    if previous and previous.cursor_row:
        parts.append(f"\x1b[{previous.cursor_row}A")
    total_rows = max(len(lines), previous.height if previous else 0)
    for row in range(total_rows):
        parts.append("\x1b[2K")
        if row < len(lines):
            parts.append(lines[row])
        if row < total_rows - 1:
            parts.append("\n\r")
    parts.append("\r")
    rows_up = total_rows - 1 - cursor_row
    if rows_up:
        parts.append(f"\x1b[{rows_up}A")
    if cursor_column:
        parts.append(f"\x1b[{cursor_column}C")
    return "".join(parts)
