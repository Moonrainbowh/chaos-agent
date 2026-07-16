from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, Sequence

from .terminal_display import DisplayEntry, DisplayKind, text_entry


class DiffLineKind(str, Enum):
    CONTEXT = "context"
    ADD = "add"
    REMOVE = "remove"
    HUNK = "hunk"


@dataclass(frozen=True)
class DiffLine:
    kind: DiffLineKind
    text: str


@dataclass(frozen=True)
class FileDiff:
    old_path: str
    new_path: str
    lines: tuple[DiffLine, ...]

    @property
    def path(self) -> str:
        return self.new_path if self.new_path != "/dev/null" else self.old_path

    @property
    def additions(self) -> int:
        return sum(line.kind is DiffLineKind.ADD for line in self.lines)

    @property
    def removals(self) -> int:
        return sum(line.kind is DiffLineKind.REMOVE for line in self.lines)


@dataclass(frozen=True)
class DiffComment:
    path: str
    line_index: int
    text: str


class GitDiffSource(Protocol):
    async def read_diff(self, paths: tuple[str, ...] = ()) -> str: ...


class DiffView:
    def __init__(self, files: Sequence[FileDiff]) -> None:
        self._files = tuple(files)
        self.selected_file = 0
        self.path_filter = ""
        self._comments: list[DiffComment] = []

    @classmethod
    def parse(cls, unified: str) -> "DiffView":
        if not isinstance(unified, str):
            raise TypeError("unified diff must be text")
        return cls(_parse_files(unified))

    @property
    def files(self) -> tuple[FileDiff, ...]:
        query = self.path_filter.casefold().strip()
        return tuple(file for file in self._files if not query or query in file.path.casefold())

    @property
    def current(self) -> FileDiff | None:
        files = self.files
        return None if not files else files[min(self.selected_file, len(files) - 1)]

    def filter(self, path: str) -> None:
        if not isinstance(path, str) or len(path) > 512:
            raise ValueError("path filter must be bounded text")
        self.path_filter = path
        self.selected_file = 0

    def move_file(self, offset: int) -> None:
        files = self.files
        if files:
            self.selected_file = (self.selected_file + offset) % len(files)

    def comment(self, line_index: int, text: str) -> DiffComment:
        current = self.current
        if current is None:
            raise ValueError("no file is selected")
        if isinstance(line_index, bool) or line_index < 0 or line_index >= len(current.lines):
            raise ValueError("line_index is outside the selected diff")
        if not isinstance(text, str) or not text.strip() or len(text) > 2_048:
            raise ValueError("comment must be bounded non-blank text")
        comment = DiffComment(current.path, line_index, text)
        self._comments.append(comment)
        return comment

    @property
    def comments(self) -> tuple[DiffComment, ...]:
        return tuple(self._comments)

    def render(self, *, max_lines: int = 200) -> tuple[DisplayEntry, ...]:
        current = self.current
        if current is None:
            return (text_entry(DisplayKind.METADATA, "no diff available"),)
        entries = [
            text_entry(
                DisplayKind.METADATA,
                f"{current.path} · +{current.additions} -{current.removals} · file {self.selected_file + 1}/{len(self.files)}",
            )
        ]
        kinds = {
            DiffLineKind.ADD: DisplayKind.DIFF_ADD,
            DiffLineKind.REMOVE: DisplayKind.DIFF_REMOVE,
            DiffLineKind.HUNK: DisplayKind.METADATA,
            DiffLineKind.CONTEXT: DisplayKind.METADATA,
        }
        entries.extend(text_entry(kinds[line.kind], line.text) for line in current.lines[:max_lines])
        if len(current.lines) > max_lines:
            entries.append(text_entry(DisplayKind.METADATA, f"... {len(current.lines) - max_lines} lines hidden"))
        return tuple(entries)


class DiffController:
    def __init__(self, source: GitDiffSource | None = None) -> None:
        self._source = source

    async def load(self, recorded_diff: str | None, paths: tuple[str, ...] = ()) -> DiffView:
        unified = recorded_diff or ""
        if self._source is not None:
            live = await self._source.read_diff(paths)
            if live.strip():
                unified = live
        return DiffView.parse(unified)


def _parse_files(unified: str) -> tuple[FileDiff, ...]:
    files: list[FileDiff] = []
    old_path: str | None = None
    new_path: str | None = None
    lines: list[DiffLine] = []

    def finish() -> None:
        nonlocal old_path, new_path, lines
        if old_path is not None and new_path is not None:
            files.append(FileDiff(old_path, new_path, tuple(lines)))
        old_path = new_path = None
        lines = []

    for raw in unified.splitlines():
        if raw.startswith("--- "):
            finish()
            old_path = _clean_path(raw[4:])
        elif raw.startswith("+++ ") and old_path is not None:
            new_path = _clean_path(raw[4:])
        elif old_path is not None and new_path is not None:
            if raw.startswith("@@"):
                kind = DiffLineKind.HUNK
            elif raw.startswith("+"):
                kind = DiffLineKind.ADD
            elif raw.startswith("-"):
                kind = DiffLineKind.REMOVE
            else:
                kind = DiffLineKind.CONTEXT
            lines.append(DiffLine(kind, raw))
    finish()
    return tuple(files)


def _clean_path(value: str) -> str:
    path = value.split("\t", 1)[0].strip()
    return re.sub(r"^[ab]/", "", path)
