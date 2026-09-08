"""Explicit capacities and independently configurable context/task limits."""
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowPolicy:
    strategy: str = "boundary"
    work_tokens: int = 256_000
    prepare_ratio: float = .75
    rotate_ratio: float = .875
    safety_tokens: int = 16_000
    task_tokens: int = 5_000_000
    handoff_tokens: int = 8_192
    keep_groups: int = 4

    def __post_init__(self):
        if self.strategy not in {"boundary", "summary", "persistent"}:
            raise ValueError("strategy must be boundary, summary, or persistent")
        for key in ("work_tokens", "task_tokens", "handoff_tokens", "keep_groups"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a positive integer")
        if isinstance(self.safety_tokens, bool) or not isinstance(self.safety_tokens, int) or self.safety_tokens < 0:
            raise ValueError("safety_tokens must be non-negative")
        if not 0 < self.prepare_ratio < self.rotate_ratio < 1:
            raise ValueError("require 0 < prepare < rotate < 1")


@dataclass(frozen=True)
class ApiContextLimits:
    combined_tokens: int
    output_tokens: int
    input_tokens: int | None = None
    source: str = "configured profile; not provider discovery"

    def __post_init__(self):
        for value in (self.combined_tokens, self.output_tokens, self.input_tokens):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ValueError("API capacities must be positive integers")
        if self.output_tokens >= self.combined_tokens:
            raise ValueError("API output reserve leaves no input capacity")

    def input_cap(self, policy):
        cap = min(policy.work_tokens, self.combined_tokens - self.output_tokens - policy.safety_tokens)
        if self.input_tokens is not None:
            cap = min(cap, self.input_tokens - policy.safety_tokens)
        if cap <= 0:
            raise ValueError("API capacity is exhausted by output and safety reserves")
        return cap


@dataclass(frozen=True)
class QueuedContextBoundary:
    request_id: str
    status: str = "queued"
