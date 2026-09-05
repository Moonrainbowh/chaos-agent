from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class InsightKind(str, Enum):
    OVERVIEW = "overview"
    CONTEXT = "context"
    IMPACT = "impact"
    TESTS = "tests"
    RISK = "risk"
    REVIEW = "review"
    REFACTOR = "refactor"
    LOCATE = "locate"
    DEAD_CODE = "dead-code"


_CONFIDENCE = frozenset({"exact", "static", "heuristic", "candidate"})


@dataclass(frozen=True)
class InsightItem:
    label: str
    detail: str
    score: int | None = None
    confidence: str = "static"

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("insight label must be non-empty text")
        if not isinstance(self.detail, str):
            raise TypeError("insight detail must be text")
        if self.score is not None and (
            isinstance(self.score, bool) or not isinstance(self.score, int)
        ):
            raise TypeError("insight score must be an integer or None")
        if self.confidence not in _CONFIDENCE:
            raise ValueError("unsupported insight confidence")


@dataclass(frozen=True)
class InsightSection:
    title: str
    items: tuple[InsightItem, ...]
    total: int | None = None
    offset: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("section title must be non-empty text")
        items = tuple(self.items)
        if not all(isinstance(item, InsightItem) for item in items):
            raise TypeError("section items must contain InsightItem values")
        object.__setattr__(self, "items", items)
        total = len(items) if self.total is None else self.total
        if isinstance(total, bool) or not isinstance(total, int) or total < len(items):
            raise ValueError("section total must be an integer at least the displayed count")
        object.__setattr__(self, "total", total)
        if (isinstance(self.offset, bool) or not isinstance(self.offset, int)
                or self.offset < 0 or self.offset + len(items) > total):
            raise ValueError("section offset must be within total")


@dataclass(frozen=True)
class SemanticInsightReport:
    kind: InsightKind
    generation: int
    title: str
    summary: str
    sections: tuple[InsightSection, ...]
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, InsightKind):
            raise TypeError("kind must be an InsightKind")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise TypeError("generation must be an integer")
        if self.generation < 0:
            raise ValueError("generation must not be negative")
        for label in ("title", "summary"):
            if not isinstance(getattr(self, label), str):
                raise TypeError(f"{label} must be text")
        sections, warnings = tuple(self.sections), tuple(self.warnings)
        if not all(isinstance(item, InsightSection) for item in sections):
            raise TypeError("sections must contain InsightSection values")
        if not all(isinstance(item, str) and item for item in warnings):
            raise TypeError("warnings must contain non-empty text")
        object.__setattr__(self, "sections", sections)
        object.__setattr__(self, "warnings", warnings)
