from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class DiffScope(str, Enum):
    WORKING_TREE = "working-tree"
    STAGED = "staged"
    UNSTAGED = "unstaged"
    UNTRACKED = "untracked"
    PER_TURN = "per-turn"
    SINCE_CHECKPOINT = "since-checkpoint"


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
    scope: DiffScope = DiffScope.PER_TURN
    fresh: bool = False

    @property
    def path(self) -> str:
        return self.new_path if self.new_path != "/dev/null" else self.old_path

    @property
    def additions(self) -> int:
        return sum(line.kind is DiffLineKind.ADD for line in self.lines)

    @property
    def removals(self) -> int:
        return sum(line.kind is DiffLineKind.REMOVE for line in self.lines)


def parse_files(
    unified: str, scope: DiffScope, fresh: bool
) -> tuple[FileDiff, ...]:
    files: list[FileDiff] = []
    state = _ParseState(scope, fresh)
    for raw in unified.splitlines():
        if state.in_hunk:
            state.consume_hunk(raw)
        elif raw.startswith("diff --git "):
            state.finish(files)
            state.start_git(raw[11:])
        elif raw.startswith("--- "):
            state.start_old(raw[4:], files)
        elif raw.startswith("+++ ") and state.old_path is not None:
            state.new_path = _header_path(raw[4:])
        elif state.old_path is not None and state.new_path is not None:
            state.append_line(raw)
    state.finish(files)
    return tuple(files)


@dataclass
class _ParseState:
    scope: DiffScope
    fresh: bool
    old_path: str | None = None
    new_path: str | None = None
    lines: list[DiffLine] = field(default_factory=list)
    git_header: bool = False
    old_remaining: int | None = None
    new_remaining: int | None = None

    @property
    def in_hunk(self) -> bool:
        return self.old_remaining is not None

    def finish(self, files: list[FileDiff]) -> None:
        if self.old_path is not None and self.new_path is not None:
            files.append(
                FileDiff(self.old_path, self.new_path, tuple(self.lines), self.scope, self.fresh)
            )
        self.old_path = self.new_path = None
        self.lines = []
        self.git_header = False
        self.old_remaining = self.new_remaining = None

    def start_git(self, value: str) -> None:
        tokens = _git_tokens(value)
        if len(tokens) >= 2:
            self.old_path = _strip_prefix(tokens[0])
            self.new_path = _strip_prefix(tokens[1])
            self.git_header = True

    def start_old(self, value: str, files: list[FileDiff]) -> None:
        if not self.git_header:
            self.finish(files)
        self.old_path = _header_path(value)

    def append_line(self, raw: str) -> None:
        self.lines.append(DiffLine(_line_kind(raw), raw))
        if not raw.startswith("@@"):
            return
        match = _HUNK_HEADER.match(raw)
        if match is None:
            return
        self.old_remaining = int(match.group(1) or "1")
        self.new_remaining = int(match.group(2) or "1")
        self._finish_hunk_if_complete()

    def consume_hunk(self, raw: str) -> None:
        self.lines.append(DiffLine(_line_kind(raw), raw))
        if raw == "\\ No newline at end of file":
            return
        assert self.old_remaining is not None and self.new_remaining is not None
        if raw.startswith("-"):
            self.old_remaining -= 1
        elif raw.startswith("+"):
            self.new_remaining -= 1
        else:
            self.old_remaining -= 1
            self.new_remaining -= 1
        self._finish_hunk_if_complete()

    def _finish_hunk_if_complete(self) -> None:
        if self.old_remaining == 0 and self.new_remaining == 0:
            self.old_remaining = self.new_remaining = None


_HUNK_HEADER = re.compile(
    r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@(?: .*)?$"
)


def _line_kind(raw: str) -> DiffLineKind:
    if raw.startswith("@@"):
        return DiffLineKind.HUNK
    if raw.startswith("+"):
        return DiffLineKind.ADD
    if raw.startswith("-"):
        return DiffLineKind.REMOVE
    return DiffLineKind.CONTEXT


def _header_path(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith('"'):
        tokens = _git_tokens(stripped)
        return _strip_prefix(tokens[0]) if tokens else ""
    return _strip_prefix(stripped.split("\t", 1)[0])


def _strip_prefix(path: str) -> str:
    return path[2:] if path.startswith(("a/", "b/")) else path


def _git_tokens(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    index = 0
    while index < len(value):
        while index < len(value) and value[index].isspace():
            index += 1
        if index >= len(value):
            break
        if value[index] == '"':
            token, index = _quoted_token(value, index + 1)
        else:
            end = index
            while end < len(value) and not value[end].isspace():
                end += 1
            token, index = value[index:end], end
        tokens.append(token)
    return tuple(tokens)


def _quoted_token(value: str, index: int) -> tuple[str, int]:
    data = bytearray()
    escapes = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13}
    while index < len(value) and value[index] != '"':
        character = value[index]
        if character != "\\":
            data.extend(character.encode("utf-8"))
            index += 1
            continue
        index += 1
        if index >= len(value):
            break
        escaped = value[index]
        if escaped in "01234567":
            end = index + 1
            while end < min(index + 3, len(value)) and value[end] in "01234567":
                end += 1
            data.append(int(value[index:end], 8))
            index = end
        else:
            data.append(escapes.get(escaped, ord(escaped)))
            index += 1
    return data.decode("utf-8"), min(index + 1, len(value))
