from __future__ import annotations

import re
from dataclasses import dataclass

from .terminal_display import display_width, graphemes, grapheme_width, safe_text
from .terminal_style import (
    BODY_WHITE,
    BRAND_CYAN,
    BRIGHT_CYAN,
    DIM_GRAY,
    ColorMode,
    colorize,
)


@dataclass(frozen=True)
class _Span:
    text: str
    code: str


def style_inline_markdown(
    value: str,
    base_code: str,
    color: ColorMode,
    *,
    allow_incomplete: bool = False,
    emphasize_label: bool = True,
) -> str:
    """Render trusted local ANSI for a small, safe inline Markdown subset."""
    spans = _inline_spans(
        safe_text(value),
        base_code,
        allow_incomplete=allow_incomplete,
        emphasize_label=emphasize_label,
    )
    return "".join(colorize(span.text, span.code, color) for span in spans)


def render_streaming_markdown_rows(
    value: str, width: int, color: ColorMode
) -> list[str]:
    """Render incomplete Markdown into width-bounded, locally styled rows."""
    rows: list[str] = []
    in_code = False
    for raw in safe_text(value).split("\n"):
        stripped = raw.strip()
        if stripped.startswith("```"):
            language = stripped[3:].strip()
            if language:
                rows.extend(_render_rows((_Span("  " + language, BRIGHT_CYAN),), width, color))
            elif not rows or rows[-1]:
                rows.append("")
            in_code = not in_code
            continue
        if in_code:
            spans = (_Span("  " + raw.rstrip(), BRAND_CYAN),)
        else:
            spans = _block_spans(raw)
        rows.extend(_render_rows(spans, width, color))
    return rows or [""]


def _block_spans(raw: str) -> tuple[_Span, ...]:
    stripped = raw.strip()
    if not stripped:
        return (_Span("", BODY_WHITE),)
    heading = re.match(r"^#{1,6}\s+(.+)$", stripped)
    if heading:
        return tuple(
            _inline_spans(
                heading.group(1), BRIGHT_CYAN,
                allow_incomplete=True, emphasize_label=False,
            )
        )
    quote = re.match(r"^>\s?(.*)$", stripped)
    if quote:
        return (
            _Span("│ ", BRAND_CYAN),
            *_inline_spans(
                quote.group(1), DIM_GRAY,
                allow_incomplete=True, emphasize_label=False,
            ),
        )
    bullet = re.match(r"^(\s*)([-*+•])\s+(.*)$", raw)
    if bullet:
        return (
            _Span(bullet.group(1) + bullet.group(2) + " ", BRAND_CYAN),
            *_inline_spans(bullet.group(3), BODY_WHITE, allow_incomplete=True),
        )
    numbered = re.match(r"^(\s*)(\d+[.)])\s+(.*)$", raw)
    if numbered:
        return (
            _Span(numbered.group(1) + numbered.group(2) + " ", BRAND_CYAN),
            *_inline_spans(numbered.group(3), BODY_WHITE, allow_incomplete=True),
        )
    return tuple(_inline_spans(stripped, BODY_WHITE, allow_incomplete=True))


def _inline_spans(
    value: str,
    base_code: str,
    *,
    allow_incomplete: bool,
    emphasize_label: bool = True,
) -> list[_Span]:
    spans: list[_Span] = []
    cursor = 0
    if emphasize_label:
        label_end = _short_label_end(value)
        if label_end:
            spans.append(_Span(value[:label_end], BRIGHT_CYAN))
            cursor = label_end
    plain_start = cursor
    while cursor < len(value):
        marker = ""
        code = base_code
        if value.startswith("**", cursor) or value.startswith("__", cursor):
            marker, code = value[cursor:cursor + 2], BRIGHT_CYAN
        elif value[cursor] == "`":
            marker, code = "`", BRAND_CYAN
        if not marker:
            cursor += 1
            continue
        if plain_start < cursor:
            spans.append(_Span(value[plain_start:cursor], base_code))
        closing = value.find(marker, cursor + len(marker))
        if closing < 0:
            if allow_incomplete:
                spans.append(_Span(value[cursor + len(marker):], code))
            else:
                spans.append(_Span(value[cursor:], base_code))
            return _merge_spans(spans)
        spans.append(_Span(value[cursor + len(marker):closing], code))
        cursor = closing + len(marker)
        plain_start = cursor
    if plain_start < len(value):
        spans.append(_Span(value[plain_start:], base_code))
    return _merge_spans(spans) or [_Span("", base_code)]


def _short_label_end(value: str) -> int:
    match = re.match(r"^([^：:\n]{1,18}[：:])", value)
    if not match:
        return 0
    label = match.group(1)
    prefix = label[:-1].strip()
    if not prefix or display_width(prefix) > 18:
        return 0
    if any(char in prefix for char in "/\\`[]{}<>，。！？,!?;；"):
        return 0
    if prefix.casefold() in {"http", "https", "file"}:
        return 0
    if len(prefix.split()) > 3:
        return 0
    return len(label)


def _render_rows(spans: tuple[_Span, ...], width: int, color: ColorMode) -> list[str]:
    wrapped = _wrap_spans(spans, max(1, width))
    return [
        "".join(colorize(span.text, span.code, color) for span in row)
        for row in wrapped
    ]


def _wrap_spans(spans: tuple[_Span, ...], width: int) -> list[list[_Span]]:
    rows: list[list[_Span]] = [[]]
    used = 0
    for span in spans:
        for cluster in graphemes(span.text):
            cluster_width = grapheme_width(cluster)
            if rows[-1] and used + cluster_width > width:
                rows.append([])
                used = 0
            visible = cluster if cluster_width <= width else "?"
            if rows[-1] and rows[-1][-1].code == span.code:
                prior = rows[-1][-1]
                rows[-1][-1] = _Span(prior.text + visible, span.code)
            else:
                rows[-1].append(_Span(visible, span.code))
            used += display_width(visible)
    return rows


def _merge_spans(spans: list[_Span]) -> list[_Span]:
    merged: list[_Span] = []
    for span in spans:
        if not span.text:
            continue
        if merged and merged[-1].code == span.code:
            merged[-1] = _Span(merged[-1].text + span.text, span.code)
        else:
            merged.append(span)
    return merged
