"""Shared AC transcript layout: section rules, left labels, right body."""
from __future__ import annotations

import re
from itertools import zip_longest

from .terminal_display import display_width
from .terminal_markdown import _Span, _inline_spans, _render_rows
from .terminal_style import BODY_WHITE, BRAND_CYAN, BRIGHT_CYAN, DIM_GRAY, ColorMode, colorize
from .terminal_theme import Theme
from .terminal_transcript_markdown import _markdown_lines


def render_ac_rows(value: str, width: int, color: ColorMode, *, incomplete: bool = False) -> list[str]:
    """Style before wrapping so emphasis cannot leak at physical row boundaries."""
    width = max(1, width)
    label_width = 12
    gutter = label_width + 2 if width >= 52 else 0
    lines = _markdown_lines(value, max(1, width - gutter), Theme.SLATE)
    rows: list[str] = []
    label = ""
    body = []
    seen_section = False
    section_number = 0
    for line in lines:
        if line.role == "heading":
            if label or body:
                rows.extend(_section_rows(label, body, width, gutter, color, incomplete))
            while rows and not rows[-1].strip():
                rows.pop()
            if seen_section:
                rows.extend(["", colorize("─" * width, DIM_GRAY, color), ""])
            elif rows:
                rows.append("")
            section_number += 1
            title = line.text
            if not re.match(r"^(?:\*\*|__)?[一二三四五六七八九十百千万零〇]+、", title):
                title = _chinese_number(section_number) + "、" + title
            label, body, seen_section = title, [], True
        else:
            body.append(line)
    rows.extend(_section_rows(label, body, width, gutter, color, incomplete))
    return rows or [""]


def _chinese_number(number: int) -> str:
    digits = "零一二三四五六七八九"
    if number < 10:
        return digits[number]
    for unit, name in ((10000, "万"), (1000, "千"), (100, "百"), (10, "十")):
        if number >= unit:
            leading, remainder = divmod(number, unit)
            prefix = "" if unit == 10 and leading == 1 else _chinese_number(leading)
            suffix = ("零" if remainder < unit // 10 else "") + _chinese_number(remainder) if remainder else ""
            return prefix + name + suffix
    raise ValueError("section number must be positive")


def _section_rows(label, lines, width, gutter, color, incomplete):
    while lines and not lines[0].text:
        lines = lines[1:]
    while lines and not lines[-1].text:
        lines = lines[:-1]
    label_plain = "".join(span.text for span in _inline_spans(
        label, BRIGHT_CYAN, allow_incomplete=incomplete, emphasize_label=False))
    # All sections share the same columns; only narrow terminals stack them.
    side = bool(label and gutter)
    content_width = width - gutter if side else width
    body = []
    for line in lines:
        body.extend(_body_rows(line, content_width, color, incomplete))
    if not label:
        return body
    if not side:
        headings = _render_rows(tuple(_inline_spans(
            label, BRIGHT_CYAN, allow_incomplete=incomplete, emphasize_label=False)), width, color)
        return headings + ([""] if body else []) + body
    headings = _render_rows((_Span(label_plain, BRIGHT_CYAN),), gutter - 2, ColorMode.NEVER)
    rows = []
    for left, right in zip_longest(headings, body, fillvalue=""):
        padding = " " * (gutter - display_width(left))
        rows.append(colorize(left, BRIGHT_CYAN, color) + padding + right if left or right else "")
    return rows


def _body_rows(line, width, color, incomplete):
    if not line.text:
        return [""]
    if line.role in {"code", "table_border", "table_header", "plan_step"}:
        code = {"code": BRAND_CYAN, "table_border": DIM_GRAY,
                "table_header": BRIGHT_CYAN, "plan_step": BRAND_CYAN}[line.role]
        return _render_rows((_Span(line.text, code),), width, color)
    prefix = "│ " if line.role == "quote" else ""
    value = line.text
    if line.role != "quote":
        match = re.match(r"^(\s*(?:[-*+•]|\d+[.)])\s+)(.*)$", value)
        if match:
            prefix, value = match.groups()
    # Keep nested list indentation and hanging continuation aligned.
    prefix_width = min(display_width(prefix), max(0, width - 1))
    if display_width(prefix) != prefix_width:
        prefix = " " * prefix_width
    spans = tuple(_inline_spans(value, BODY_WHITE,
                               allow_incomplete=incomplete, emphasize_label=False))
    rows = _render_rows(spans, max(1, width - prefix_width), color)
    return [colorize(prefix, BRAND_CYAN, color) + rows[0],
            *(" " * prefix_width + row for row in rows[1:])]
