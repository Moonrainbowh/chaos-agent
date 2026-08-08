from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Union


PathValue = Union[str, Path]
_BUILTIN_PARTS = frozenset(
    {".git", ".chaos-agent", ".code-agent", "chaos-agent-workspaces", "__pycache__"}
)


@dataclass(frozen=True)
class _Rule:
    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool
    has_slash: bool
    regex: re.Pattern[str]

    @classmethod
    def parse(cls, line: str) -> "_Rule | None":
        value = line.rstrip("\r\n")
        if not value or value.startswith("#"):
            return None
        escaped_leading = value.startswith("\\#") or value.startswith("\\!")
        if escaped_leading:
            value = value[1:]
        negated = not escaped_leading and value.startswith("!")
        if negated:
            value = value[1:]
        if not value:
            return None
        directory_only = value.endswith("/")
        value = value.rstrip("/")
        anchored = value.startswith("/")
        value = value.lstrip("/")
        if not value:
            return None
        has_slash = "/" in value
        return cls(
            pattern=value,
            negated=negated,
            directory_only=directory_only,
            anchored=anchored,
            has_slash=has_slash,
            regex=re.compile(_glob_regex(value)),
        )

    def matches(self, path: str, *, is_dir: bool) -> bool:
        if self.directory_only:
            parts = path.split("/")
            directory_count = len(parts) if is_dir else len(parts) - 1
            return any(
                self._matches_path("/".join(parts[:index]))
                for index in range(1, directory_count + 1)
            )
        return self._matches_path(path)

    def _matches_path(self, path: str) -> bool:
        if self.anchored or self.has_slash:
            return self.regex.fullmatch(path) is not None
        return any(self.regex.fullmatch(part) for part in path.split("/"))


class IgnoreRules:
    """A deterministic, commonly-used subset of root .gitignore rules."""

    def __init__(self, rules: Iterable[_Rule] = ()) -> None:
        self._rules = tuple(rules)

    @classmethod
    def from_workspace(cls, root: PathValue) -> "IgnoreRules":
        root_path = Path(root)
        ignore_file = root_path / ".gitignore"
        if not ignore_file.is_file():
            return cls()
        try:
            lines = ignore_file.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError):
            return cls()
        return cls(rule for line in lines if (rule := _Rule.parse(line)) is not None)

    def is_ignored(self, path: PathValue, *, is_dir: bool = False) -> bool:
        relative = _normalize(path)
        if not relative:
            return False
        if self.is_builtin_ignored(relative):
            return True
        ignored = False
        for rule in self._rules:
            if rule.matches(relative, is_dir=is_dir):
                ignored = not rule.negated
        return ignored

    @staticmethod
    def is_builtin_ignored(path: PathValue) -> bool:
        parts = _normalize(path).split("/")
        return any(part.casefold() in _BUILTIN_PARTS for part in parts)


def _normalize(path: PathValue) -> str:
    return str(path).replace("\\", "/").removeprefix("./").strip("/")


def _glob_regex(pattern: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 1
                if index + 1 < len(pattern) and pattern[index + 1] == "/":
                    index += 1
                    output.append("(?:.*/)?")
                else:
                    output.append(".*")
            else:
                output.append("[^/]*")
        elif char == "?":
            output.append("[^/]")
        elif char == "[":
            end = pattern.find("]", index + 1)
            if end == -1:
                output.append("\\[")
            else:
                content = pattern[index + 1 : end]
                if content.startswith("!"):
                    content = "^" + content[1:]
                output.append("[" + content.replace("\\", "\\\\") + "]")
                index = end
        else:
            output.append(re.escape(char))
        index += 1
    return "".join(output)
