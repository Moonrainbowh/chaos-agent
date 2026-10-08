from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class BudgetDiagnostic:
    """Safe local budget facts; never instruction text or upstream bodies."""
    files: tuple[str, ...]
    counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        for path in self.files:
            pure = PurePosixPath(path)
            if not path or pure.is_absolute() or '..' in pure.parts or '\\' in path or ':' in path:
                raise ValueError('diagnostic files must be workspace-relative POSIX paths')
            if any(ord(char) < 32 for char in path):
                raise ValueError('diagnostic path cannot contain control characters')
        allowed = {'rendered_tokens', 'max_rule_tokens', 'total_bytes', 'max_rules_total',
                   'system_and_rules_tokens', 'rule_tokens', 'tool_tokens', 'task_state_tokens',
                   'max_prompt_tokens', 'max_system_and_rules_tokens', 'min_message_tokens',
                   'safety_tokens'}
        if any(key not in allowed or type(value) is not int or value < 0 for key, value in self.counts):
            raise ValueError('diagnostic counts must be known non-negative integers')


class ContextError(Exception):
    """Base exception for context construction failures."""

    def __init__(self, message: str, *, diagnostic: BudgetDiagnostic | None = None) -> None:
        super().__init__(message)
        if diagnostic is not None and not isinstance(diagnostic, BudgetDiagnostic):
            raise TypeError('diagnostic must be BudgetDiagnostic')
        self.diagnostic = diagnostic


class RuleLimitError(ContextError):
    """Raised when project rules exceed a configured hard limit."""


class ContextBudgetError(ContextError):
    """Raised when a context budget cannot be satisfied."""


class PromptBudgetError(ContextError):
    """Raised when prompt budget limits or allocations are invalid."""


class RepoMapError(ContextError):
    """Raised when bounded repository discovery cannot start or finish."""
