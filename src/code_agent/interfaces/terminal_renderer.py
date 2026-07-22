from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .terminal_display import DisplayEntry, DisplayKind, display_width, safe_text
from .terminal_style import (
    BODY_WHITE,
    BRAND_CYAN,
    BRIGHT_CYAN,
    DIM_GRAY,
    ERROR_RED,
    SUCCESS_GREEN,
    TOOL_GRAY,
    WARNING_YELLOW,
    ColorMode,
    colorize,
)
from .terminal_tail import render_live_tail


class Theme(str, Enum):
    SIGNAL = "signal"
    SYMBOL = "symbol"
    PLAIN = "plain"


_MARKERS = {
    DisplayKind.USER: ">", DisplayKind.AGENT: "*", DisplayKind.PARTIAL_AGENT: "!", DisplayKind.TOOL: ":",
    DisplayKind.SUCCESS: "+", DisplayKind.WARNING: "!", DisplayKind.ERROR: "x",
    DisplayKind.METADATA: ".", DisplayKind.DIFF_ADD: "+", DisplayKind.DIFF_REMOVE: "-",
}
_SYMBOLS = {**_MARKERS, DisplayKind.USER: "›", DisplayKind.AGENT: "◆", DisplayKind.TOOL: "↳", DisplayKind.SUCCESS: "✓", DisplayKind.ERROR: "×"}
_COLORS = {
    DisplayKind.USER: BRAND_CYAN, DisplayKind.AGENT: BRAND_CYAN, DisplayKind.PARTIAL_AGENT: WARNING_YELLOW,
    DisplayKind.TOOL: TOOL_GRAY, DisplayKind.SUCCESS: SUCCESS_GREEN,
    DisplayKind.WARNING: WARNING_YELLOW, DisplayKind.ERROR: ERROR_RED,
    DisplayKind.METADATA: DIM_GRAY, DisplayKind.DIFF_ADD: BRAND_CYAN,
    DisplayKind.DIFF_REMOVE: ERROR_RED,
}


@dataclass(frozen=True)
class _RenderLine:
    text: str
    role: str = "body"


def render_entry(entry: DisplayEntry, width: int, *, theme: Theme = Theme.SYMBOL, color: ColorMode = ColorMode.AUTO) -> str:
    marker = (_SYMBOLS if theme is Theme.SYMBOL else _MARKERS)[entry.kind]
    prefix = f"[{marker}]" if theme is Theme.PLAIN else marker
    code = _COLORS.get(entry.kind)
    content_width = max(1, width - display_width(prefix) - 1)
    if entry.kind is DisplayKind.AGENT:
        lines = _markdown_lines(entry.text, content_width, theme)
    elif entry.kind is DisplayKind.PARTIAL_AGENT:
        body = [_RenderLine(line) for line in safe_text(entry.text).splitlines() or [""]]
        lines = [_RenderLine("未完成回答", "partial_label"), *body]
    else:
        lines = [_RenderLine(line) for line in entry.text.splitlines() or [""]]
    rendered = []
    for index, line in enumerate(lines):
        leader = prefix if index == 0 else " " * display_width(prefix)
        for part in _wrap_display(line.text, max(1, width - display_width(leader) - 1)):
            rendered.append(_style_line(leader, part, code, color, role=line.role, kind=entry.kind))
            leader = " " * display_width(prefix)
    return "\n".join(rendered)


def render_entries(
    entries: Iterable[DisplayEntry], width: int, *, theme: Theme, color: ColorMode, previous: DisplayEntry | None = None
) -> str:
    rendered: list[str] = []
    prior = previous
    for entry in entries:
        if prior is not None and _needs_gap(prior, entry):
            rendered.append("")
        rendered.append(render_entry(entry, width, theme=theme, color=color))
        prior = entry
    return "\n".join(rendered)


def _markdown_lines(value: str, width: int, theme: Theme) -> list[_RenderLine]:
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
            lines.extend(_format_table(rows, width, theme))
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


def _format_table(rows: list[list[str]], width: int, theme: Theme) -> list[_RenderLine]:
    columns = len(rows[0])
    available = max(columns, width)
    if available < columns * 3:
        return [_RenderLine(f"{row[0]}: " + " · ".join(row[1:]), "table_header" if index == 0 else "body") for index, row in enumerate(rows)]
    minimum = max(3, available // columns)
    widths = [max(display_width(row[column]) for row in rows) for column in range(columns)]
    while sum(widths) > available:
        widest = max(range(columns), key=widths.__getitem__)
        if widths[widest] <= minimum:
            break
        widths[widest] -= 1
    rule = ("─" if theme is Theme.SYMBOL else "-") * min(width, sum(widths) + 3 * (columns - 1))
    lines = [_RenderLine(rule, "table_border")]
    for row_index, row in enumerate(rows):
        cells = [_wrap_display(cell, widths[column]) for column, cell in enumerate(row)]
        height = max(len(cell) for cell in cells)
        for line_index in range(height):
            padded = [_pad_display(cell[line_index] if line_index < len(cell) else "", widths[column]) for column, cell in enumerate(cells)]
            lines.append(_RenderLine("   ".join(padded), "table_header" if row_index == 0 else "body"))
        if row_index == 0:
            lines.append(_RenderLine(rule, "table_border"))
    lines.append(_RenderLine(rule, "table_border"))
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


def _style_line(leader: str, value: str, code: str | None, color: ColorMode, *, role: str, kind: DisplayKind) -> str:
    plain = f"{leader} {value}"
    styled_leader = colorize(leader, code, color) if leader.strip() else leader
    if role == "partial_label":
        body_code = WARNING_YELLOW
    elif role in {"heading", "table_header"}:
        body_code = BRIGHT_CYAN
    elif role == "table_border":
        body_code = DIM_GRAY
    elif role == "code":
        body_code = BRAND_CYAN
    elif kind is DisplayKind.SUCCESS:
        body_code = SUCCESS_GREEN
    elif kind in {DisplayKind.USER, DisplayKind.AGENT, DisplayKind.PARTIAL_AGENT}:
        body_code = BODY_WHITE
    elif kind is DisplayKind.TOOL:
        body_code = TOOL_GRAY
    else:
        body_code = code
    styled_value = colorize(value, body_code, color)
    return f"{styled_leader} {styled_value}"


def _needs_gap(previous: DisplayEntry, current: DisplayEntry) -> bool:
    return current.kind is DisplayKind.USER or (previous.kind is DisplayKind.TOOL and current.kind is DisplayKind.AGENT)
