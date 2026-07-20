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
        if raw.startswith("diff --git "):
            state.finish(files)
            state.start_git(raw[11:])
        elif state.in_hunk:
            state.consume_hunk(raw)
        elif raw.startswith("--- "):
            state.start_old(raw[4:], files)
        elif raw.startswith("+++ ") and state.old_path is not None:
            state.start_new(raw[4:])
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
    valid: bool = True

    @property
    def in_hunk(self) -> bool:
        return self.old_remaining is not None

    def finish(self, files: list[FileDiff]) -> None:
        if self.in_hunk:
            self.valid = False
        if self.valid and self.old_path is not None and self.new_path is not None:
            files.append(
                FileDiff(self.old_path, self.new_path, tuple(self.lines), self.scope, self.fresh)
            )
        self.old_path = self.new_path = None
        self.lines = []
        self.git_header = False
        self.old_remaining = self.new_remaining = None
        self.valid = True

    def start_git(self, value: str) -> None:
        tokens = _git_tokens(value)
        self.git_header = True
        if tokens is None or len(tokens) < 2 or not tokens[0] or not tokens[1]:
            self.valid = False
            return
        self.old_path = _strip_prefix(tokens[0])
        self.new_path = _strip_prefix(tokens[1])

    def start_old(self, value: str, files: list[FileDiff]) -> None:
        if not self.git_header:
            self.finish(files)
        path = _header_path(value)
        if path is None:
            self.valid = False
            return
        self.old_path = path

    def start_new(self, value: str) -> None:
        path = _header_path(value)
        if path is None:
            self.valid = False
            return
        self.new_path = path

    def append_line(self, raw: str) -> None:
        self.lines.append(DiffLine(_line_kind(raw), raw))
        if not raw.startswith("@@"):
            return
        match = _HUNK_HEADER.match(raw)
        if match is None:
            self.valid = False
            self.old_remaining = self.new_remaining = None
            return
        self.old_remaining = int(match.group(1) or "1")
        self.new_remaining = int(match.group(2) or "1")
        self._finish_hunk_if_complete()

    def consume_hunk(self, raw: str) -> None:
        if raw == "\\ No newline at end of file":
            self.lines.append(DiffLine(_line_kind(raw), raw))
            return
        if not raw.startswith(("-", "+", " ")):
            self.valid = False
            self.old_remaining = self.new_remaining = None
            return
        self.lines.append(DiffLine(_line_kind(raw), raw))
        assert self.old_remaining is not None and self.new_remaining is not None
        if raw.startswith("-"):
            self.old_remaining -= 1
        elif raw.startswith("+"):
            self.new_remaining -= 1
        else:
            self.old_remaining -= 1
            self.new_remaining -= 1
        if self.old_remaining < 0 or self.new_remaining < 0:
            self.valid = False
            self.old_remaining = self.new_remaining = None
            return
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


def _header_path(value: str) -> str | None:
    stripped = value.strip()
    if stripped.startswith('"'):
        tokens = _git_tokens(stripped)
        return _strip_prefix(tokens[0]) if tokens and tokens[0] else None
    path = stripped.split("\t", 1)[0]
    return _strip_prefix(path) if path else None


def _strip_prefix(path: str) -> str:
    return path[2:] if path.startswith(("a/", "b/")) else path


def _git_tokens(value: str) -> tuple[str, ...] | None:
    tokens: list[str] = []
    index = 0
    while index < len(value):
        while index < len(value) and value[index].isspace():
            index += 1
        if index >= len(value):
            break
        if value[index] == '"':
            quoted = _quoted_token(value, index + 1)
            if quoted is None:
                return None
            token, index = quoted
        else:
            end = index
            while end < len(value) and not value[end].isspace():
                end += 1
            token, index = value[index:end], end
        tokens.append(token)
    return tuple(tokens)


def _quoted_token(value: str, index: int) -> tuple[str, int] | None:
    data = bytearray()
    escapes = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13}
    try:
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
        if index >= len(value) or value[index] != '"':
            return None
        return data.decode("utf-8"), index + 1
    except (ValueError, UnicodeDecodeError):
        return None
