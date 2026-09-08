from __future__ import annotations

import re
from dataclasses import dataclass
from .terminal_display import display_width, safe_text
from .terminal_theme import Theme


@dataclass(frozen=True)
class _RenderLine:
    text: str
    role: str = "body"


def _format_plan_card(plan_text: str, width: int, theme: Theme) -> list[_RenderLine]:
    raw_lines = [line.strip() for line in plan_text.strip().splitlines() if line.strip()]
    if not raw_lines:
        return []
    box_w = min(width, 76)
    rule = "─" * max(10, box_w - 18)
    lines: list[_RenderLine] = [
        _RenderLine(f"┌── Task plan {rule}┐", "table_border")
    ]
    for raw in raw_lines:
        item = re.sub(r"^\d+\.\s*", "", raw)
        lines.append(_RenderLine(f"│ [ ] {item}", "plan_step"))
    lines.append(_RenderLine(f"└{'─' * max(10, box_w - 2)}┘", "table_border"))
    return lines


def _markdown_lines(value: str, width: int, theme: Theme) -> list[_RenderLine]:
    # Extract <plan>...</plan> or <replan>...</replan> blocks for trajectory rendering
    plan_match = re.search(r"<(?:plan|replan)>(.*?)</(?:plan|replan)>", value, re.DOTALL)
    plan_lines: list[_RenderLine] = []
    if plan_match:
        plan_content = plan_match.group(1)
        plan_lines = _format_plan_card(plan_content, width, theme)
        value = (value[:plan_match.start()] + "\n" + value[plan_match.end():]).strip()

    source = safe_text(value).splitlines()
    lines: list[_RenderLine] = []
    if plan_lines:
        lines.extend(plan_lines)
        lines.append(_RenderLine(""))

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
        if heading and any(line.role == "heading" for line in lines):
            lines.extend([_RenderLine("─" * max(1, width), "table_border"), _RenderLine("")])
        if heading:
            lines.append(_RenderLine(_inline_markdown(heading.group(1)), "heading")); index += 1; continue
        quote = re.match(r"^>\s*(.+)$", stripped)
        if quote:
            lines.append(_RenderLine(quote.group(1), "quote")); index += 1; continue
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
    rule = ("─" if theme not in {Theme.PLAIN, Theme.SIGNAL} else "-") * min(width, sum(widths) + 3 * (columns - 1))
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


