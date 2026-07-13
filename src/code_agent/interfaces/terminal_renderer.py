from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .terminal_display import DisplayEntry, DisplayKind, clip_display, display_width, safe_text


class ColorMode(str, Enum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


class Theme(str, Enum):
    SIGNAL = "signal"
    SYMBOL = "symbol"
    PLAIN = "plain"


_MARKERS = {
    DisplayKind.USER: ">", DisplayKind.AGENT: "*", DisplayKind.TOOL: ":",
    DisplayKind.SUCCESS: "+", DisplayKind.WARNING: "!", DisplayKind.ERROR: "x",
    DisplayKind.METADATA: ".", DisplayKind.DIFF_ADD: "+", DisplayKind.DIFF_REMOVE: "-",
}
_SYMBOLS = {**_MARKERS, DisplayKind.USER: "›", DisplayKind.AGENT: "◆", DisplayKind.TOOL: "↳", DisplayKind.SUCCESS: "✓", DisplayKind.ERROR: "×"}
_COLORS = {
    DisplayKind.USER: "38;5;80", DisplayKind.AGENT: "38;5;121",
    DisplayKind.TOOL: "38;5;153", DisplayKind.SUCCESS: "38;5;114",
    DisplayKind.WARNING: "33", DisplayKind.ERROR: "31", DisplayKind.METADATA: "38;5;245",
    DisplayKind.DIFF_ADD: "32", DisplayKind.DIFF_REMOVE: "31",
}


@dataclass(frozen=True)
class _RenderLine:
    text: str
    role: str = "body"


def color_enabled(mode: ColorMode, env: dict[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return mode is ColorMode.ALWAYS or (mode is ColorMode.AUTO and not source.get("NO_COLOR"))


def render_entry(entry: DisplayEntry, width: int, *, theme: Theme = Theme.SIGNAL, color: ColorMode = ColorMode.AUTO) -> str:
    marker = (_SYMBOLS if theme is Theme.SYMBOL else _MARKERS)[entry.kind]
    prefix = f"[{marker}]" if theme is Theme.PLAIN else marker
    code = _COLORS.get(entry.kind)
    content_width = max(1, width - display_width(prefix) - 1)
    lines = _markdown_lines(entry.text, content_width) if entry.kind is DisplayKind.AGENT else [_RenderLine(line) for line in entry.text.splitlines() or [""]]
    rendered = []
    for index, line in enumerate(lines):
        leader = prefix if index == 0 else " " * display_width(prefix)
        for part in _wrap_display(line.text, max(1, width - display_width(leader) - 1)):
            value = f"{leader} {part}"
            rendered.append(_style_line(value, code, color, role=line.role))
            leader = " " * display_width(prefix)
    return "\n".join(rendered)


def render_entries(entries: Iterable[DisplayEntry], width: int, *, theme: Theme, color: ColorMode) -> str:
    return "\n".join(render_entry(entry, width, theme=theme, color=color) for entry in entries)


def _markdown_lines(value: str, width: int) -> list[_RenderLine]:
    source = safe_text(value).splitlines()
    lines: list[_RenderLine] = []
    in_code = False
    index = 0
    while index < len(source):
        raw = source[index]
        stripped = raw.strip()
        table = _parse_table(source, index)
        if not in_code and table is not None:
            rows, index = table
            lines.extend(_format_table(rows, width))
            continue
        if stripped.startswith("```"):
            in_code = not in_code
            if stripped[3:].strip(): lines.append(_RenderLine("  " + stripped[3:].strip(), "code"))
            index += 1
            continue
        if not stripped:
            if lines and lines[-1].text: lines.append(_RenderLine(""))
            index += 1
            continue
        if in_code:
            lines.append(_RenderLine("  " + raw.rstrip(), "code")); index += 1; continue
        heading = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if heading:
            lines.append(_RenderLine(_inline_markdown(heading.group(1)), "heading")); index += 1; continue
        lines.append(_RenderLine(_inline_markdown(stripped)))
        index += 1
    return lines or [_RenderLine("")]


def _parse_table(source: list[str], index: int) -> tuple[list[list[str]], int] | None:
    header = _split_table_row(source[index])
    if header is None or index + 1 >= len(source) or not _is_table_divider(source[index + 1], len(header)):
        return None
    rows = [header]
    cursor = index + 2
    while cursor < len(source):
        row = _split_table_row(source[cursor])
        if row is None or len(row) != len(header):
            break
        rows.append(row)
        cursor += 1
    return rows, cursor


def _split_table_row(value: str) -> list[str] | None:
    stripped = value.strip()
    if "|" not in stripped:
        return None
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    cells = [_inline_markdown(cell.strip()) for cell in stripped.split("|")]
    return cells if len(cells) > 1 and all(cells) else None


def _is_table_divider(value: str, columns: int) -> bool:
    cells = _split_table_row(value)
    return bool(cells and len(cells) == columns and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells))


def _format_table(rows: list[list[str]], width: int) -> list[_RenderLine]:
    columns = len(rows[0])
    available = max(columns, width - columns - 1)
    if available < columns * 3:
        return [_RenderLine(" | ".join(row), "table_header" if index == 0 else "body") for index, row in enumerate(rows)]
    minimum = max(3, available // columns)
    widths = [max(display_width(row[column]) for row in rows) + 2 for column in range(columns)]
    while sum(widths) > available:
        widest = max(range(columns), key=widths.__getitem__)
        if widths[widest] <= minimum:
            break
        widths[widest] -= 1
    border = lambda: "+" + "+".join("-" * item for item in widths) + "+"
    lines = [_RenderLine(border(), "table_border")]
    for row_index, row in enumerate(rows):
        cells = [_wrap_display(cell, widths[column] - 2) for column, cell in enumerate(row)]
        height = max(len(cell) for cell in cells)
        for line_index in range(height):
            padded = [" " + _pad_display(cell[line_index] if line_index < len(cell) else "", widths[column] - 2) + " " for column, cell in enumerate(cells)]
            lines.append(_RenderLine("|" + "|".join(padded) + "|", "table_header" if row_index == 0 else "body"))
        if row_index == 0:
            lines.append(_RenderLine(border(), "table_border"))
    lines.append(_RenderLine(border(), "table_border"))
    return lines


def _inline_markdown(value: str) -> str:
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    return value


def _wrap_display(value: str, width: int) -> list[str]:
    if value == "": return [""]
    result, current, used = [], [], 0
    for char in value:
        char_width = display_width(char)
        if current and used + char_width > width:
            result.append("".join(current)); current, used = [], 0
        current.append(char); used += char_width
    result.append("".join(current))
    return result


def _pad_display(value: str, width: int) -> str:
    return value + " " * max(0, width - display_width(value))


def _style_line(value: str, code: str | None, color: ColorMode, *, role: str) -> str:
    if not color_enabled(color): return value
    if role in {"heading", "table_header"}: return f"\x1b[1;96m{value}\x1b[0m"
    if role == "table_border": return f"\x1b[2m{value}\x1b[0m"
    if role == "code": return f"\x1b[38;5;153m{value}\x1b[0m"
    return f"\x1b[{code}m{value}\x1b[0m" if code else value


def render_live_tail(input_text: str, status: str, width: int, *, cursor_index: int | None = None, color: ColorMode = ColorMode.AUTO, palette: Iterable[str] = (), status_icon: str = ".", status_color: str | None = None) -> str:
    """Redraw only the current input and its single status line, never screen history."""
    inner_width = max(1, width - 4)
    supplied = clip_display(safe_text(input_text), inner_width)
    placeholder = "输入任务、编辑请求，或输入 / 查看命令"
    visible = supplied or clip_display(placeholder, inner_width)
    padding = " " * max(0, inner_width - display_width(visible))
    plain_input = "[> " + visible + padding + "]"
    suggestions = "  ".join(clip_display(safe_text(item), width) for item in palette)
    plain_status = safe_text(status_icon) + " " + safe_text(status)
    if suggestions:
        plain_status += " | " + suggestions
    input_line = plain_input
    status_line = clip_display(plain_status, width)
    if color_enabled(color):
        content = supplied or f"\x1b[2m{visible}\x1b[0m"
        input_line = f"\x1b[36m[> \x1b[0m{content}\x1b[36m{padding}]\x1b[0m"
        status_line = f"\x1b[{status_color}m{status_line}\x1b[0m" if status_color else f"\x1b[2m{status_line}\x1b[0m"
    index = len(supplied) if cursor_index is None else min(max(0, cursor_index), len(supplied))
    cursor = 3 + display_width(supplied[:index])
    return "\r\x1b[2K" + input_line + "\n\r\x1b[2K" + status_line + f"\x1b[1A\r\x1b[{cursor}C"
