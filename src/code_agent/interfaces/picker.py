from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .terminal_display import clip_display, safe_text


class PickerSource(str, Enum):
    COMMAND = "command"
    SESSION = "session"
    MODE = "mode"
    SKILL = "skill"
    MCP = "mcp"
    PLUGIN = "plugin"


@dataclass(frozen=True)
class PickerItem:
    identifier: str
    label: str
    source: PickerSource
    detail: str = ""
    keywords: tuple[str, ...] = ()
    enabled: bool = True
    disabled_reason: str | None = None
    completion: str | None = None

    def __post_init__(self) -> None:
        for name in ("identifier", "label"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f"{name} must be non-blank bounded text")
        if not isinstance(self.source, PickerSource):
            raise TypeError("source must be PickerSource")
        if not isinstance(self.detail, str) or len(self.detail) > 512:
            raise ValueError("detail must be bounded text")
        keywords = tuple(self.keywords)
        if any(not isinstance(value, str) or not value.strip() for value in keywords):
            raise ValueError("keywords must contain non-blank text")
        object.__setattr__(self, "keywords", keywords)
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be bool")
        if not self.enabled and not self.disabled_reason:
            raise ValueError("disabled item requires a reason")
        if self.completion is not None and not isinstance(self.completion, str):
            raise TypeError("completion must be text or None")


@dataclass(frozen=True)
class PickerSelection:
    item: PickerItem
    completion: str


class PickerState:
    def __init__(self, items: Iterable[PickerItem] = (), *, limit: int = 6) -> None:
        if isinstance(limit, bool) or limit <= 0 or limit > 20:
            raise ValueError("limit must be between 1 and 20")
        self._items = _unique(items)
        self._limit = limit
        self.query = ""
        self.selected_index = 0
        self.error: str | None = None

    def set_items(self, items: Iterable[PickerItem]) -> None:
        updated = _unique(items)
        if updated == self._items:
            return
        self._items = updated
        self.selected_index = 0
        self.error = None

    def update_query(self, query: str) -> None:
        if not isinstance(query, str) or len(query) > 512:
            raise ValueError("query must be bounded text")
        if query != self.query:
            self.query = query
            self.selected_index = 0
            self.error = None

    @property
    def matches(self) -> tuple[PickerItem, ...]:
        terms = tuple(part for part in self.query.casefold().split() if part)
        ranked: list[tuple[int, int, PickerItem]] = []
        for order, item in enumerate(self._items):
            text = " ".join((item.label, item.identifier, item.detail, *item.keywords)).casefold()
            if not all(term in text for term in terms):
                continue
            label = item.label.casefold().lstrip("/")
            label_tail = label.rsplit(" ", 1)[-1]
            keywords = tuple(value.casefold() for value in item.keywords)
            score = sum(
                100 if term == label
                else 50 if term == label_tail
                else 40 if term in keywords
                else 4 if label.startswith(term)
                else 1
                for term in terms
            )
            ranked.append((-score, order, item))
        ranked.sort(key=lambda value: (value[0], value[1]))
        return tuple(value[2] for value in ranked)

    @property
    def visible(self) -> tuple[PickerItem, ...]:
        matches = self.matches
        if len(matches) <= self._limit:
            return matches
        start = min(max(0, self.selected_index - self._limit + 1), len(matches) - self._limit)
        return matches[start : start + self._limit]

    def move(self, offset: int) -> None:
        matches = self.matches
        if not matches:
            self.selected_index = 0
            return
        self.selected_index = (self.selected_index + offset) % len(matches)
        self.error = None

    @property
    def selected(self) -> PickerItem | None:
        matches = self.matches
        if not matches:
            return None
        return matches[min(self.selected_index, len(matches) - 1)]

    def accept(self) -> PickerSelection | None:
        selected = self.selected
        if selected is None:
            self.error = "no matching item"
            return None
        if not selected.enabled:
            self.error = selected.disabled_reason or "item is disabled"
            return None
        self.error = None
        return PickerSelection(selected, selected.completion or selected.identifier)

    def rows(self, width: int) -> tuple[str, ...]:
        rows: list[str] = []
        selected = self.selected
        for item in self.visible:
            marker = "›" if item == selected else " "
            suffix = item.detail
            if not item.enabled:
                suffix = item.disabled_reason or "disabled"
            text = f"{marker} {item.label}" + (f" · {suffix}" if suffix else "")
            rows.append(clip_display(safe_text(text), width))
        if len(self.matches) > self._limit:
            rows.append(clip_display(f"  {self.selected_index + 1}/{len(self.matches)}", width))
        if self.error:
            rows.append(clip_display("! " + safe_text(self.error), width))
        return tuple(rows)


def command_picker_items(
    specs: Iterable[object], services: set[str] | None = None, *, parent: object | None = None
) -> tuple[PickerItem, ...]:
    available = services or set()
    result = []
    values = parent.actions if parent is not None else specs
    for spec in values:
        if parent is None and spec.actions:
            missing = tuple(value for value in spec.requires if value not in available)
            for action in spec.actions:
                result.append(
                    PickerItem(
                        f"{spec.name}:{action.name}",
                        f"/{spec.name} {action.name}",
                        PickerSource.COMMAND,
                        action.description,
                        (*spec.aliases, *action.aliases),
                        enabled=not missing,
                        disabled_reason=("requires " + ", ".join(missing)) if missing else None,
                        completion=f"/{spec.name} {action.name}",
                    )
                )
            continue
        requirements = (
            *getattr(parent, "requires", ()),
            *getattr(spec, "requires", ()),
        )
        missing = tuple(dict.fromkeys(value for value in requirements if value not in available))
        prefix = f"/{parent.name} " if parent is not None else "/"
        has_next = bool(spec.usage or (parent is None and spec.actions))
        result.append(
            PickerItem(
                (parent.name + ":" if parent is not None else "") + spec.name,
                prefix + spec.name,
                PickerSource.COMMAND,
                spec.description,
                tuple(spec.aliases),
                enabled=not missing,
                disabled_reason=("requires " + ", ".join(missing)) if missing else None,
                completion=prefix + spec.name + (" " if has_next else ""),
            )
        )
    return tuple(result)


def _unique(items: Iterable[PickerItem]) -> tuple[PickerItem, ...]:
    values = tuple(items)
    if any(not isinstance(item, PickerItem) for item in values):
        raise TypeError("items must contain PickerItem values")
    keys = [(item.source, item.identifier) for item in values]
    if len(keys) != len(set(keys)):
        raise ValueError("picker items must be unique per source")
    return values
