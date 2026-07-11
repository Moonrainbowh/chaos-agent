from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional, Tuple

from code_agent.core.models import Message

from .budget import PromptBudget
from .errors import PromptBudgetError


def _positive_integer(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value <= 0:
        raise ValueError(f"{label} must be positive")


def _nonnegative_integer(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must not be negative")


def _stable_path(value: object, label: str) -> Path:
    try:
        raw = os.fspath(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise TypeError(f"{label} must be a path") from error
    if not isinstance(raw, str):
        raise TypeError(f"{label} must be a text path")
    if not raw.strip() or "\0" in raw:
        raise ValueError(f"{label} must be a non-empty path")
    return Path(raw).expanduser().resolve(strict=False)


@dataclass(frozen=True)
class ContextConfig:
    workspace_root: Path
    cwd: Path
    system_prompt: str
    max_rule_bytes: int = 64_000
    max_rules_total: int = 256_000
    repo_scan: int = 5_000
    repo_map_tokens: Optional[int] = None
    message_tokens: Optional[int] = None
    recent_messages: int = 12
    prompt_budget: PromptBudget = field(default_factory=PromptBudget)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "workspace_root", _stable_path(self.workspace_root, "workspace_root")
        )
        object.__setattr__(self, "cwd", _stable_path(self.cwd, "cwd"))
        if not isinstance(self.system_prompt, str):
            raise TypeError("system_prompt must be text")
        if not self.system_prompt.strip():
            raise ValueError("system_prompt must be non-empty")
        for label in (
            "max_rule_bytes",
            "max_rules_total",
            "repo_scan",
            "recent_messages",
        ):
            _positive_integer(getattr(self, label), label)
        self._normalize_prompt_budget()

    def _normalize_prompt_budget(self) -> None:
        if not isinstance(self.prompt_budget, PromptBudget):
            raise TypeError("prompt_budget must be a PromptBudget")
        for label in ("repo_map_tokens", "message_tokens"):
            value = getattr(self, label)
            if value is not None:
                _positive_integer(value, label)

        default_budget = PromptBudget()
        aliases_supplied = (
            self.repo_map_tokens is not None or self.message_tokens is not None
        )
        if aliases_supplied and self.prompt_budget != default_budget:
            if (
                self.repo_map_tokens is not None
                and self.repo_map_tokens != self.prompt_budget.max_repo_map_tokens
            ) or (
                self.message_tokens is not None
                and self.message_tokens != self.prompt_budget.max_message_tokens
            ):
                raise PromptBudgetError(
                    "legacy token aliases conflict with prompt_budget ceilings"
                )

        if aliases_supplied and self.prompt_budget == default_budget:
            repo_map_tokens = self.repo_map_tokens or 1_200
            message_tokens = self.message_tokens or 8_000
            normalized = replace(
                default_budget,
                max_repo_map_tokens=repo_map_tokens,
                max_message_tokens=message_tokens,
                min_message_tokens=min(default_budget.min_message_tokens, message_tokens),
            )
        elif self.prompt_budget == default_budget:
            normalized = replace(
                default_budget,
                max_repo_map_tokens=1_200,
                max_message_tokens=8_000,
            )
        else:
            normalized = self.prompt_budget

        object.__setattr__(self, "prompt_budget", normalized)
        object.__setattr__(self, "repo_map_tokens", normalized.max_repo_map_tokens)
        object.__setattr__(self, "message_tokens", normalized.max_message_tokens)


@dataclass(frozen=True)
class ProjectRule:
    path: str
    content: str
    scope_depth: int

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("path must be non-empty text")
        if not isinstance(self.content, str):
            raise TypeError("content must be text")
        _nonnegative_integer(self.scope_depth, "scope_depth")


@dataclass(frozen=True)
class Symbol:
    path: str
    name: str
    kind: str
    line: int

    def __post_init__(self) -> None:
        for label in ("path", "name", "kind"):
            if not isinstance(getattr(self, label), str) or not getattr(self, label):
                raise ValueError(f"{label} must be non-empty text")
        _positive_integer(self.line, "line")


@dataclass(frozen=True)
class RepoEntry:
    path: str
    symbols: Tuple[Symbol, ...] = field(default_factory=tuple)
    dependencies: Tuple[str, ...] = field(default_factory=tuple)
    size_bytes: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("path must be non-empty text")
        symbols = tuple(self.symbols)
        dependencies = tuple(self.dependencies)
        if not all(isinstance(item, Symbol) for item in symbols):
            raise TypeError("symbols must contain only Symbol values")
        if not all(isinstance(item, str) and item for item in dependencies):
            raise TypeError("dependencies must contain non-empty paths")
        _nonnegative_integer(self.size_bytes, "size_bytes")
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "dependencies", dependencies)


@dataclass(frozen=True)
class CompactionResult:
    messages: Tuple[Message, ...]
    removed_count: int
    estimated_tokens: int
    summary: Optional[str] = None

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        if not all(isinstance(item, Message) for item in messages):
            raise TypeError("messages must contain only Message values")
        _nonnegative_integer(self.removed_count, "removed_count")
        _nonnegative_integer(self.estimated_tokens, "estimated_tokens")
        if self.summary is not None and not isinstance(self.summary, str):
            raise TypeError("summary must be text or None")
        object.__setattr__(self, "messages", messages)
