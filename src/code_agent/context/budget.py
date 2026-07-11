from __future__ import annotations

from dataclasses import dataclass

from .errors import PromptBudgetError


def _nonnegative_token_count(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PromptBudgetError(f"{label} must be an integer")
    if value < 0:
        raise PromptBudgetError(f"{label} must not be negative")
    return value


def _positive_token_count(value: object, label: str) -> int:
    value = _nonnegative_token_count(value, label)
    if value == 0:
        raise PromptBudgetError(f"{label} must be positive")
    return value


@dataclass(frozen=True)
class PromptAllocation:
    """The token allocation for one complete model prompt."""

    rule_tokens: int
    tool_tokens: int
    task_state_tokens: int
    repo_map_tokens: int
    message_tokens: int
    safety_tokens: int

    def __post_init__(self) -> None:
        for label in (
            "rule_tokens",
            "tool_tokens",
            "task_state_tokens",
            "repo_map_tokens",
            "message_tokens",
            "safety_tokens",
        ):
            _nonnegative_token_count(getattr(self, label), label)

    @property
    def total_tokens(self) -> int:
        return (
            self.rule_tokens
            + self.tool_tokens
            + self.task_state_tokens
            + self.repo_map_tokens
            + self.message_tokens
            + self.safety_tokens
        )


@dataclass(frozen=True)
class PromptBudget:
    """Ceilings used to reserve deterministic prompt capacity."""

    max_prompt_tokens: int = 20_000
    max_rule_tokens: int = 3_000
    max_tool_tokens: int = 1_500
    max_task_state_tokens: int = 1_000
    max_repo_map_tokens: int = 2_000
    max_message_tokens: int = 12_000
    min_message_tokens: int = 2_000
    safety_tokens: int = 500

    def __post_init__(self) -> None:
        for label in (
            "max_prompt_tokens",
            "max_rule_tokens",
            "max_tool_tokens",
            "max_task_state_tokens",
            "max_repo_map_tokens",
            "max_message_tokens",
            "min_message_tokens",
        ):
            _positive_token_count(getattr(self, label), label)
        _nonnegative_token_count(self.safety_tokens, "safety_tokens")
        if self.min_message_tokens > self.max_message_tokens:
            raise PromptBudgetError(
                "min_message_tokens cannot exceed max_message_tokens"
            )
        if self.max_prompt_tokens < self.safety_tokens + self.min_message_tokens:
            raise PromptBudgetError(
                "max_prompt_tokens cannot cover safety and minimum messages"
            )

    def allocate(
        self,
        *,
        system_and_rules_tokens: int = 0,
        tool_tokens: int = 0,
        task_state_tokens: int = 0,
    ) -> PromptAllocation:
        """Allocate a bounded prompt, dropping map capacity before messages."""
        fixed = (
            ("system_and_rules_tokens", system_and_rules_tokens, self.max_rule_tokens),
            ("tool_tokens", tool_tokens, self.max_tool_tokens),
            ("task_state_tokens", task_state_tokens, self.max_task_state_tokens),
        )
        for label, value, ceiling in fixed:
            _nonnegative_token_count(value, label)
            if value > ceiling:
                raise PromptBudgetError(f"{label} exceeds its configured ceiling")

        remaining = (
            self.max_prompt_tokens
            - self.safety_tokens
            - system_and_rules_tokens
            - tool_tokens
            - task_state_tokens
        )
        if remaining < self.min_message_tokens:
            raise PromptBudgetError("fixed prompt content cannot retain minimum messages")

        message_tokens = min(self.max_message_tokens, remaining)
        repo_map_tokens = min(self.max_repo_map_tokens, remaining - message_tokens)
        return PromptAllocation(
            rule_tokens=system_and_rules_tokens,
            tool_tokens=tool_tokens,
            task_state_tokens=task_state_tokens,
            repo_map_tokens=repo_map_tokens,
            message_tokens=message_tokens,
            safety_tokens=self.safety_tokens,
        )
