from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .terminal_display import DisplayEntry, DisplayKind, display_width, safe_text, text_entry
from .terminal_style import (
    BODY_WHITE,
    BORDER_GRAY,
    BRAND_CYAN,
    BRIGHT_CYAN,
    DIM_GRAY,
    ERROR_RED,
    SUCCESS_GREEN,
    TOOL_GRAY,
    WARNING_YELLOW,
    ColorMode,
    color_enabled,
    colorize,
)
from .terminal_tail import render_live_tail


class Theme(str, Enum):
    SIGNAL = "signal"
    SYMBOL = "symbol"
    PLAIN = "plain"
    MODERN = "modern"


_MARKERS = {
    DisplayKind.USER: ">", DisplayKind.AGENT: "*", DisplayKind.PARTIAL_AGENT: "!", DisplayKind.TOOL: ":",
    DisplayKind.SUCCESS: "+", DisplayKind.WARNING: "!", DisplayKind.ERROR: "x",
    DisplayKind.METADATA: ".", DisplayKind.DIFF_ADD: "+", DisplayKind.DIFF_REMOVE: "-",
}
_SYMBOLS = {**_MARKERS, DisplayKind.USER: "›", DisplayKind.AGENT: "◆", DisplayKind.TOOL: "↳", DisplayKind.SUCCESS: "✓", DisplayKind.ERROR: "×"}
_MODERN = {**_SYMBOLS, DisplayKind.USER: "❯", DisplayKind.AGENT: "✦", DisplayKind.TOOL: "├─", DisplayKind.SUCCESS: "✓", DisplayKind.METADATA: "·"}
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
    if theme is Theme.MODERN:
        marker = _MODERN[entry.kind]
    elif theme is Theme.SYMBOL:
        marker = _SYMBOLS[entry.kind]
    else:
        marker = _MARKERS[entry.kind]
    prefix = f"[{marker}]" if theme is Theme.PLAIN else marker
    code = _COLORS.get(entry.kind)
    content_width = max(1, width - display_width(prefix) - 1)
    if entry.kind is DisplayKind.AGENT:
        lines = _markdown_lines(entry.text, content_width, theme)
        if theme is Theme.MODERN:
            lines = [_RenderLine("Chaos Agent", "agent_header"), *lines]
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


def _fold_tool_entries(entries: list[DisplayEntry]) -> list[DisplayEntry]:
    result: list[DisplayEntry] = []
    tool_group: list[DisplayEntry] = []

    def flush_tools() -> None:
        if not tool_group:
            return
        if len(tool_group) == 1:
            single_text = tool_group[0].text
            first_line = single_text.splitlines()[0] if single_text else "Tool completed"
            result.append(text_entry(DisplayKind.TOOL, first_line))
        else:
            valid = [e for e in tool_group if "load_tool_contract" not in e.text]
            if not valid:
                valid = tool_group
            read_count = sum(1 for e in valid if "Read file" in e.text)
            has_list = any("List files" in e.text for e in valid)
            has_verify = any("Run verification" in e.text for e in valid)
            has_edit = any("Edit file" in e.text or "Write file" in e.text for e in valid)

            parts = []
            if has_list:
                parts.append("扫描目录")
            if read_count > 0:
                parts.append(f"读取 {read_count} 个文件")
            if has_edit:
                parts.append("编辑代码")
            if has_verify:
                parts.append("工程验证")

            detail = " · ".join(parts) if parts else "多项操作"
            summary = f"已执行 {len(valid)} 项工具 ({detail})"
            result.append(text_entry(DisplayKind.TOOL, summary))
        tool_group.clear()

    for entry in entries:
        if entry.kind is DisplayKind.TOOL:
            tool_group.append(entry)
        else:
            flush_tools()
            result.append(entry)
    flush_tools()
    return result


def render_entries(
    entries: Iterable[DisplayEntry], width: int, *, theme: Theme, color: ColorMode, previous: DisplayEntry | None = None
) -> str:
    rendered: list[str] = []
    prior = previous
    entry_list = list(entries)
    if theme is Theme.MODERN:
        entry_list = _fold_tool_entries(entry_list)
    for entry in entry_list:
        if prior is not None and _needs_gap(prior, entry):
            rendered.append("")
        rendered.append(render_entry(entry, width, theme=theme, color=color))
        prior = entry
    return "\n".join(rendered)


def _format_plan_card(plan_text: str, width: int, theme: Theme) -> list[_RenderLine]:
    raw_lines = [line.strip() for line in plan_text.strip().splitlines() if line.strip()]
    if not raw_lines:
        return []
    box_w = min(width, 76)
    rule = "─" * max(10, box_w - 18)
    lines: list[_RenderLine] = [
        _RenderLine(f"┌── 任务决策轨迹 {rule}┐", "table_border")
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
    rule = ("─" if theme in {Theme.SYMBOL, Theme.MODERN} else "-") * min(width, sum(widths) + 3 * (columns - 1))
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


def _highlight_inline_spans(value: str, base_code: str, color: ColorMode) -> str:
    if not color_enabled(color):
        value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
        value = re.sub(r"`([^`]+)`", r"\1", value)
        return colorize(value, base_code, color)

    bullet_match = re.match(r"^(\s*[-*•])\s+(.+)$", value)
    if bullet_match:
        bullet_sym = bullet_match.group(1)
        rest = bullet_match.group(2)
        styled_bullet = colorize(bullet_sym, BRAND_CYAN, color)
        return f"{styled_bullet} {_highlight_inline_spans(rest, base_code, color)}"

    num_match = re.match(r"^(\s*\d+\.)\s+(.+)$", value)
    if num_match:
        num_sym = num_match.group(1)
        rest = num_match.group(2)
        styled_num = colorize(num_sym, BRAND_CYAN, color)
        return f"{styled_num} {_highlight_inline_spans(rest, base_code, color)}"

    quote_match = re.match(r"^(\s*>)\s*(.+)$", value)
    if quote_match:
        quote_prefix = colorize("│", BORDER_GRAY, color)
        return f"{quote_prefix} {colorize(quote_match.group(2), DIM_GRAY, color)}"

    def _replace_code(m: re.Match[str]) -> str:
        return f"\x1b[{BRAND_CYAN}m{m.group(1)}\x1b[0m\x1b[{base_code}m"

    def _replace_bold(m: re.Match[str]) -> str:
        return f"\x1b[1;38;5;255m{m.group(1)}\x1b[0m\x1b[{base_code}m"

    val = re.sub(r"`([^`]+)`", _replace_code, value)
    val = re.sub(r"\*\*(.+?)\*\*", _replace_bold, val)
    return colorize(val, base_code, color)


def _style_line(leader: str, value: str, code: str | None, color: ColorMode, *, role: str, kind: DisplayKind) -> str:
    plain = f"{leader} {value}"
    styled_leader = colorize(leader, code, color) if leader.strip() else leader
    if role == "agent_header":
        return colorize(f"{leader} {value}", BRIGHT_CYAN, color)
    elif role == "quote":
        styled_leader = colorize("│", BRAND_CYAN, color)
        styled_value = _highlight_inline_spans(value, DIM_GRAY, color)
        return f"{styled_leader} {styled_value}"
    elif role == "partial_label":
        body_code = WARNING_YELLOW
    elif role in {"heading", "table_header"}:
        body_code = BRIGHT_CYAN
    elif role == "table_border":
        body_code = DIM_GRAY
    elif role == "plan_step":
        body_code = BRAND_CYAN
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

    if kind in {DisplayKind.AGENT, DisplayKind.USER} and role not in {"code", "heading", "table_header", "table_border", "quote", "agent_header"}:
        styled_value = _highlight_inline_spans(value, body_code, color)
    else:
        styled_value = colorize(value, body_code, color)
    return f"{styled_leader} {styled_value}"


def _needs_gap(previous: DisplayEntry, current: DisplayEntry) -> bool:
    return current.kind is DisplayKind.USER or (previous.kind is DisplayKind.TOOL and current.kind is DisplayKind.AGENT)
