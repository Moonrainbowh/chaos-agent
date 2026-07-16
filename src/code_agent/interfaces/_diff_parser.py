from __future__ import annotations

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
        if raw.startswith("diff --git "):
            state.finish(files)
            state.start_git(raw[11:])
        elif raw.startswith("--- "):
            state.start_old(raw[4:], files)
        elif raw.startswith("+++ ") and state.old_path is not None:
            state.new_path = _header_path(raw[4:])
        elif state.old_path is not None and state.new_path is not None:
            state.lines.append(DiffLine(_line_kind(raw), raw))
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

    def finish(self, files: list[FileDiff]) -> None:
        if self.old_path is not None and self.new_path is not None:
            files.append(
                FileDiff(self.old_path, self.new_path, tuple(self.lines), self.scope, self.fresh)
            )
        self.old_path = self.new_path = None
        self.lines = []
        self.git_header = False

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
