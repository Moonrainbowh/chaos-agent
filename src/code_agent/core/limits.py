from __future__ import annotations

from dataclasses import dataclass

from .models import Usage


@dataclass(frozen=True)
class EngineLimits:
    max_model_turns: int = 20
    max_tool_calls: int = 50
    max_total_tokens: int = 200_000
    max_assistant_chars: int = 1_000_000

    def __post_init__(self) -> None:
        values = {
            "max_model_turns": (self.max_model_turns, 1),
            "max_tool_calls": (self.max_tool_calls, 0),
            "max_total_tokens": (self.max_total_tokens, 1),
            "max_assistant_chars": (self.max_assistant_chars, 1),
        }
        for name, (value, minimum) in values.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < minimum:
                raise ValueError(f"{name} must be at least {minimum}")


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
