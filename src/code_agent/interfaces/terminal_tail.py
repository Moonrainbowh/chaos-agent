from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .terminal_display import (
    clip_display,
    display_width,
    graphemes,
    grapheme_width,
    safe_text,
)
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
    safe_width = max(1, width)
    safe_height = max(1, terminal_height)
    if safe_width < 7 or safe_height < 4:
        return _compact_frame(
            input_text, status, safe_width, safe_height, cursor_index, previous
        )
    return _normal_frame(
        input_text, status, safe_width, safe_height, cursor_index,
        assistant_draft, color, tuple(palette), status_icon, status_color,
        status_context, previous,
    )


def _normal_frame(
    input_text: str, status: str, width: int, height: int,
    cursor_index: int | None, draft: str, color: ColorMode,
    palette: tuple[str, ...], status_icon: str, status_color: str | None,
    status_context: str, previous: LiveTailGeometry | None,
) -> LiveTailFrame:
    frame_width = max(7, width - 1)
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
    input_budget = min(len(rows), height - 3)
    rows, cursor_row = _visible_input_rows(rows, cursor_row, input_budget)
    remaining = height - len(rows) - 3
    palette_items = tuple(safe_text(item).replace("\n", " ") for item in palette)[
        : min(14, remaining)
    ]
    remaining -= len(palette_items)
    draft_lines = _render_draft(draft, width, max(0, remaining - 1), color)
    lines = _render_palette(palette_items, width, color) + draft_lines
    lines.append(_style_box_border(top_border, color))
    for row_index, row in enumerate(rows):
        prompt = "› " if row_index == 0 else "  "
        padding = " " * max(0, text_width - display_width(row))
        lines.append(_style_box_row(prompt, row, padding, color, placeholder=placeholder_visible))
    lines.append(_style_box_border(bottom_border, color))

    lines.append(_render_status(status_icon, status, status_context, width, color, status_color))

    geometry = LiveTailGeometry(height=len(lines), cursor_row=cursor_row + 1 + len(palette_items) + len(draft_lines))
    output = _rewrite_tail(
        lines, geometry.cursor_row, cursor_column + 4, previous, height
    )
    return LiveTailFrame(output, geometry)


def _compact_frame(
    input_text: str, status: str, width: int, height: int,
    cursor_index: int | None, previous: LiveTailGeometry | None,
) -> LiveTailFrame:
    supplied = safe_text(input_text)
    index = len(supplied) if cursor_index is None else min(max(0, cursor_index), len(supplied))
    rows, cursor_row, cursor_column = _layout_input(supplied, max(1, width - 2), index)
    active = rows[cursor_row] if supplied else ""
    composer = clip_display(("› " if width > 1 else "") + active, width)
    lines = [composer]
    if height > 1:
        lines.append(clip_display(safe_text(status).replace("\n", " "), width))
    geometry = LiveTailGeometry(len(lines), 0)
    column = min(max(0, width - 1), cursor_column + (2 if width > 1 else 0))
    return LiveTailFrame(_rewrite_tail(lines, 0, column, previous, height), geometry)


def _visible_input_rows(
    rows: list[str], cursor_row: int, budget: int
) -> tuple[list[str], int]:
    start = min(max(0, cursor_row - budget + 1), max(0, len(rows) - budget))
    return rows[start:start + budget], cursor_row - start


def clear_live_tail(
    geometry: LiveTailGeometry | None, *, terminal_height: int | None = None
) -> str:
    """Erase a rendered dynamic tail and return the cursor to its top row."""
    if geometry is None:
        return "\r\x1b[2K"
    height = geometry.height
    if terminal_height is not None:
        height = min(height, max(1, terminal_height))
    cursor_row = min(geometry.cursor_row, height - 1)
    parts = ["\r"]
    if cursor_row:
        parts.append(f"\x1b[{cursor_row}A")
    for row in range(height):
        parts.append("\x1b[2K")
        if row < height - 1:
            parts.append("\n\r")
    parts.append("\r")
    if height > 1:
        parts.append(f"\x1b[{height - 1}A")
    return "".join(parts)


def _layout_input(value: str, width: int, cursor_index: int) -> tuple[list[str], int, int]:
    rows = [""]
    row_widths = [0]
    cursor_row = 0
    cursor_column = 0
    offset = 0
    for cluster in graphemes(value):
        if offset <= cursor_index < offset + len(cluster):
            cursor_row, cursor_column = len(rows) - 1, row_widths[-1]
        if cluster == "\n":
            rows.append("")
            row_widths.append(0)
            offset += len(cluster)
            continue
        cluster_width = grapheme_width(cluster)
        if rows[-1] and row_widths[-1] + cluster_width > width:
            rows.append("")
            row_widths.append(0)
        visible = cluster if cluster_width <= width else "?"
        rows[-1] += visible
        row_widths[-1] += display_width(visible)
        offset += len(cluster)
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
    if not value or max_rows <= 0:
        return []
    title = clip_display("◆ 正在回答", width)
    prefix = "  " if width > 2 else ""
    rows = _wrap_plain(safe_text(value), max(1, width - display_width(prefix)))
    clipped = rows[-max_rows:]
    if len(rows) > len(clipped):
        clipped[0] = clip_display("… " + clipped[0], width - display_width(prefix))
    return [colorize(title, BRAND_CYAN, color)] + [
        colorize(prefix + row, BODY_WHITE, color) for row in clipped
    ]


def _wrap_plain(value: str, width: int) -> list[str]:
    rows: list[str] = []
    for logical in value.split("\n"):
        clusters = list(graphemes(logical))
        if not clusters:
            rows.append("")
        while clusters:
            part, used = _take_row(clusters, width)
            rows.append(part)
            del clusters[:used]
    return rows


def _take_row(clusters: list[str], width: int) -> tuple[str, int]:
    result: list[str] = []
    used_width = 0
    for index, cluster in enumerate(clusters):
        cluster_width = grapheme_width(cluster)
        if cluster_width > width and not result:
            return "?", 1
        if used_width + cluster_width > width:
            return "".join(result), index
        result.append(cluster)
        used_width += cluster_width
    return "".join(result), len(clusters)


def _render_status(
    icon: str, status: str, context: str, width: int, color: ColorMode, status_color: str | None
) -> str:
    left = clip_display(safe_text(icon) + " " + safe_text(status).replace("\n", " "), width)
    right = safe_text(context).replace("\n", " ")
    if right and display_width(left) + display_width(right) + 2 <= width:
        padding = " " * (width - display_width(left) - display_width(right))
        return colorize(left, status_color or DIM_GRAY, color) + padding + colorize(right, DIM_GRAY, color)
    return colorize(left, status_color or DIM_GRAY, color)


def _rewrite_tail(
    lines: list[str], cursor_row: int, cursor_column: int,
    previous: LiveTailGeometry | None, terminal_height: int,
) -> str:
    parts = ["\r"]
    previous_cursor = min(
        previous.cursor_row if previous else 0, terminal_height - 1
    )
    if previous_cursor:
        parts.append(f"\x1b[{previous_cursor}A")
    previous_height = min(previous.height if previous else 0, terminal_height)
    total_rows = min(terminal_height, max(len(lines), previous_height))
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
