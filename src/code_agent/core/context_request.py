from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

from ._json import JSONValue, freeze_mapping
from .cancellation import CancellationToken
from .limits import TaskBudget
from .models import Message, ToolDefinition
from .attachments import AttachmentRef, freeze_attachments
from .task_state import TaskState


def _finite_number(value: object, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def budget_lease(budget: TaskBudget) -> dict[str, int]:
    """Expose only numeric usage and limits to context builders."""
    limits = budget.limits
    return {
        "model_turns": budget.model_turns,
        "tool_calls": budget.tool_calls,
        "input_tokens": budget.input_tokens,
        "output_tokens": budget.output_tokens,
        "max_model_turns": limits.max_model_turns,
        "max_tool_calls": limits.max_tool_calls,
        "max_tool_calls_per_round": limits.max_tool_calls_per_round,
        "max_total_tokens": limits.max_total_tokens,
    }


def _freeze_budget_lease(
    value: Mapping[str, JSONValue],
) -> Mapping[str, JSONValue]:
    frozen = freeze_mapping(value, "budget_lease")
    for name, item in frozen.items():
        if isinstance(item, bool) or not isinstance(item, int):
            raise TypeError(f"budget_lease.{name} must be an integer")
        if item < 0:
            raise ValueError(f"budget_lease.{name} must not be negative")
    return frozen


@dataclass(frozen=True)
class ContextRequest:
    """Immutable input for one context-building revision."""

    thread_id: str
    revision: int
    messages: tuple[Message, ...]
    user_input: str
    tools: tuple[ToolDefinition, ...]
    task_state: TaskState
    cancellation: CancellationToken
    mode_snapshot: Mapping[str, JSONValue] = field(default_factory=dict)
    permission_snapshot: Mapping[str, JSONValue] = field(default_factory=dict)
    context_pressure: float | None = None
    timeout_seconds: float | None = None
    budget_lease: Mapping[str, JSONValue] = field(default_factory=dict)
    attachments: tuple[AttachmentRef, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.thread_id, str):
            raise TypeError("thread_id must be a string")
        if not self.thread_id.strip():
            raise ValueError("thread_id must not be blank")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise TypeError("revision must be an integer")
        if self.revision <= 0:
            raise ValueError("revision must be positive")
        if not isinstance(self.user_input, str):
            raise TypeError("user_input must be a string")
        if not isinstance(self.messages, tuple):
            raise TypeError("messages must be a tuple")
        if not all(isinstance(item, Message) for item in self.messages):
            raise TypeError("messages must contain only Message values")
        if not isinstance(self.tools, tuple):
            raise TypeError("tools must be a tuple")
        if not all(isinstance(item, ToolDefinition) for item in self.tools):
            raise TypeError("tools must contain only ToolDefinition values")
        if not isinstance(self.task_state, TaskState):
            raise TypeError("task_state must be a TaskState")
        if not isinstance(self.cancellation, CancellationToken):
            raise TypeError("cancellation must be a CancellationToken")
        self._validate_limits()
        for name in ("mode_snapshot", "permission_snapshot"):
            object.__setattr__(self, name, freeze_mapping(getattr(self, name), name))
        object.__setattr__(self, "budget_lease", _freeze_budget_lease(self.budget_lease))
        object.__setattr__(self, "attachments", freeze_attachments(self.attachments))

    def _validate_limits(self) -> None:
        if self.context_pressure is not None:
            pressure = _finite_number(self.context_pressure, "context_pressure")
            if not 0 <= pressure <= 1:
                raise ValueError("context_pressure must be between zero and one")
        if self.timeout_seconds is not None:
            timeout = _finite_number(self.timeout_seconds, "timeout_seconds")
            if timeout <= 0:
                raise ValueError("timeout_seconds must be positive")
