from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .terminal_display import DisplayEntry, DisplayKind, display_width, safe_text, text_entry
from .terminal_markdown import style_inline_markdown
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
    colorize,
)
from .terminal_tail import render_live_tail


from .terminal_theme import Theme, design_for, recolor
from .terminal_transcript_markdown import _RenderLine, _markdown_lines, _wrap_display


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


def render_entry(entry: DisplayEntry, width: int, *, theme: Theme = Theme.SYMBOL, color: ColorMode = ColorMode.AUTO) -> str:
    if theme is Theme.MODERN or design_for(theme):
        marker = _MODERN[entry.kind]
    elif theme is Theme.SYMBOL:
        marker = _SYMBOLS[entry.kind]
    else:
        marker = _MARKERS[entry.kind]
    if design_for(theme):
        if entry.kind is DisplayKind.TOOL:
            marker = "↳"
        if theme is Theme.MONO and entry.kind is DisplayKind.AGENT:
            marker = "·"
    prefix = f"[{marker}]" if theme is Theme.PLAIN else marker
    code = _COLORS.get(entry.kind)
    content_width = max(1, width - display_width(prefix) - 1)
    if entry.kind is DisplayKind.AGENT:
        lines = _markdown_lines(entry.text, content_width, theme)
        if theme is Theme.MODERN or design_for(theme):
            label = "CHAOS / RESPONSE" if theme is Theme.EMBER else "Chaos Agent"
            lines = [_RenderLine(label, "agent_header"), *lines]
    elif entry.kind is DisplayKind.PARTIAL_AGENT:
        body = [_RenderLine(line) for line in safe_text(entry.text).splitlines() or [""]]
        lines = [_RenderLine("Incomplete response", "partial_label"), *body]
    else:
        lines = [_RenderLine(line) for line in entry.text.splitlines() or [""]]
    rendered = []
    for index, line in enumerate(lines):
        leader = prefix if index == 0 else " " * display_width(prefix)
        for part in _wrap_display(line.text, max(1, width - display_width(leader) - 1)):
            rendered.append(_style_line(leader, part, code, color, role=line.role, kind=entry.kind))
            leader = " " * display_width(prefix)
    return recolor("\n".join(rendered), theme)


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
                parts.append("listed files")
            if read_count > 0:
                parts.append(f"read {read_count} files")
            if has_edit:
                parts.append("edited code")
            if has_verify:
                parts.append("project verification")

            detail = " · ".join(parts) if parts else "multiple actions"
            summary = f"Executed {len(valid)} tools ({detail})"
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


def _highlight_inline_spans(value: str, base_code: str, color: ColorMode) -> str:
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

    return style_inline_markdown(value, base_code, color)


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

    if kind in {DisplayKind.AGENT, DisplayKind.USER} and role in {"heading", "table_header"}:
        styled_value = style_inline_markdown(
            value, body_code, color, emphasize_label=False
        )
    elif kind in {DisplayKind.AGENT, DisplayKind.USER} and role not in {"code", "table_border", "quote", "agent_header"}:
        styled_value = _highlight_inline_spans(value, body_code, color)
    else:
        styled_value = colorize(value, body_code, color)
    return f"{styled_leader} {styled_value}"


def _needs_gap(previous: DisplayEntry, current: DisplayEntry) -> bool:
    return current.kind is DisplayKind.USER or (previous.kind is DisplayKind.TOOL and current.kind is DisplayKind.AGENT)
