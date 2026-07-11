from __future__ import annotations


class ContextError(Exception):
    """Base exception for context construction failures."""


class RuleLimitError(ContextError):
    """Raised when project rules exceed a configured hard limit."""


class ContextBudgetError(ContextError):
    """Raised when a context budget cannot be satisfied."""


class PromptBudgetError(ContextError):
    """Raised when prompt budget limits or allocations are invalid."""


class RepoMapError(ContextError):
    """Raised when bounded repository discovery cannot start or finish."""
