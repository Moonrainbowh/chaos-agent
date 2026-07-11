from __future__ import annotations

from dataclasses import dataclass

from .models import Usage


@dataclass(frozen=True, init=False)
class EngineLimits:
    max_agent_rounds: int
    max_tool_calls: int
    max_tool_calls_per_round: int
    max_total_tokens: int
    max_assistant_chars: int

    def __init__(
        self,
        max_agent_rounds: int = 50,
        max_tool_calls: int = 128,
        max_tool_calls_per_round: int = 50,
        max_total_tokens: int = 200_000,
        max_assistant_chars: int = 1_000_000,
        *,
        max_model_turns: int | None = None,
    ) -> None:
        if max_model_turns is not None:
            if max_agent_rounds != 50:
                raise ValueError("max_agent_rounds conflicts with max_model_turns")
            max_agent_rounds = max_model_turns
        object.__setattr__(self, "max_agent_rounds", max_agent_rounds)
        object.__setattr__(self, "max_tool_calls", max_tool_calls)
        object.__setattr__(self, "max_tool_calls_per_round", max_tool_calls_per_round)
        object.__setattr__(self, "max_total_tokens", max_total_tokens)
        object.__setattr__(self, "max_assistant_chars", max_assistant_chars)
        self.__post_init__()

    @property
    def max_model_turns(self) -> int:
        """Compatibility alias for callers predating max_agent_rounds."""
        return self.max_agent_rounds

    def __post_init__(self) -> None:
        values = {
            "max_agent_rounds": (self.max_agent_rounds, 1),
            "max_tool_calls": (self.max_tool_calls, 0),
            "max_tool_calls_per_round": (self.max_tool_calls_per_round, 1),
            "max_total_tokens": (self.max_total_tokens, 1),
            "max_assistant_chars": (self.max_assistant_chars, 1),
        }
        for name, (value, minimum) in values.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < minimum:
                raise ValueError(f"{name} must be at least {minimum}")


@dataclass(frozen=True)
class TaskBudget:
    model_name: str
    limits: EngineLimits
    model_turns: int = 0
    tool_calls: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise ValueError("model_name must be non-blank text")
        if not isinstance(self.limits, EngineLimits):
            raise TypeError("limits must be EngineLimits")
        for name in ("model_turns", "tool_calls"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.model_turns > self.limits.max_agent_rounds:
            raise ValueError("model_turns exceeds task limit")
        if self.tool_calls > self.limits.max_tool_calls:
            raise ValueError("tool_calls exceeds task limit")


def add_usage(left: Usage, right: Usage) -> Usage:
    return Usage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cached_input_tokens=left.cached_input_tokens + right.cached_input_tokens,
    )


def usage_payload(usage: Usage) -> dict[str, int]:
    result = usage.to_dict()
    result["total_tokens"] = usage.total_tokens
    return result
