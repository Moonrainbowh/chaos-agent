from __future__ import annotations

import re
import unicodedata
import regex
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class DisplayKind(str, Enum):
    USER = "user"
    AGENT = "agent"
    PARTIAL_AGENT = "partial_agent"
    TOOL = "tool"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    METADATA = "metadata"
    DIFF_ADD = "diff_add"
    DIFF_REMOVE = "diff_remove"


@dataclass(frozen=True)
class DisplaySpan:
    text: str
    role: str = "text"

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")


@dataclass(frozen=True)
class DisplayEntry:
    kind: DisplayKind
    spans: tuple[DisplaySpan, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DisplayKind):
            raise TypeError("kind must be a DisplayKind")
        if not self.spans:
            raise ValueError("display entry requires at least one span")

    @property
    def text(self) -> str:
        return "".join(span.text for span in self.spans)


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def safe_text(value: object) -> str:
    """Remove terminal controls from untrusted values while retaining line breaks."""
    if not isinstance(value, str):
        return ""
    return _CONTROL.sub("?", value.replace("\x1b", "?")).replace("\r\n", "\n")


def display_width(value: str) -> int:
    return sum(grapheme_width(cluster) for cluster in graphemes(value))


def clip_display(value: str, width: int) -> str:
    if width <= 0:
        return ""
    result: list[str] = []
    used = 0
    for cluster in graphemes(value):
        cluster_width = grapheme_width(cluster)
        if used + cluster_width > width:
            break
        result.append(cluster)
        used += cluster_width
    return "".join(result)


def graphemes(value: str) -> tuple[str, ...]:
    """Split text into Unicode extended grapheme clusters."""
    return tuple(regex.findall(r"\X", value))


def grapheme_width(cluster: str) -> int:
    """Return a stable terminal width without splitting joined glyphs."""
    if not cluster:
        return 0
    if regex.fullmatch(r"\p{Regional_Indicator}{2}", cluster):
        return 2
    widths = [_codepoint_width(char) for char in cluster]
    if "\u200d" in cluster or regex.search(r"\p{Extended_Pictographic}", cluster):
        return max(widths, default=0)
    if len(cluster) > 1:
        return max(widths, default=0)
    return widths[0]


def _codepoint_width(char: str) -> int:
    category = unicodedata.category(char)
    if category.startswith("C") or category in {"Mn", "Me"}:
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1


def text_entry(kind: DisplayKind, text: object) -> DisplayEntry:
    return DisplayEntry(kind, (DisplaySpan(safe_text(text)),))


def flatten_entries(entries: Iterable[DisplayEntry]) -> list[str]:
    return [entry.text for entry in entries]
