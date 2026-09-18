from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .models import Usage

if TYPE_CHECKING:
    from .task import TaskContract


class BudgetLeaseTier(str, Enum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class BudgetReserveStatus(str, Enum):
    RESERVED = "reserved"
    RENEWED = "renewed"
    LEASE_EXHAUSTED = "lease_exhausted"
    HARD_EXHAUSTED = "hard_exhausted"


_LEASE_LIMITS = {
    BudgetLeaseTier.QUICK: (4, 8),
    BudgetLeaseTier.STANDARD: (12, 30),
    BudgetLeaseTier.DEEP: (30, 80),
}
_DEEP_REQUEST = re.compile(
    r"(?:深度|穷尽|全仓库|跨模块|彻底|全面调查|deep|exhaustive|"
    r"repository[ -]wide|cross[ -]module)",
    re.IGNORECASE,
)


def select_budget_lease(contract: TaskContract) -> BudgetLeaseTier:
    """Choose an initial lease without changing the task's authorization."""
    from .completion_contract import TaskIntent
    from .task import TaskContract

    if not isinstance(contract, TaskContract):
        raise TypeError("contract must be a TaskContract")
    if _DEEP_REQUEST.search(contract.objective):
        return BudgetLeaseTier.DEEP
    if contract.intent is TaskIntent.ANALYZE:
        return BudgetLeaseTier.QUICK
    return BudgetLeaseTier.STANDARD


def lease_limits(
    tier: BudgetLeaseTier, limits: EngineLimits
) -> tuple[int, int]:
    if not isinstance(tier, BudgetLeaseTier):
        raise TypeError("tier must be a BudgetLeaseTier")
    if not isinstance(limits, EngineLimits):
        raise TypeError("limits must be EngineLimits")
    turns, tools = _LEASE_LIMITS[tier]
    return min(turns, limits.max_agent_rounds), min(tools, limits.max_tool_calls)


@dataclass(frozen=True)
class TaskProgressSnapshot:
    """Bounded Host facts used to decide whether a soft lease may renew."""

    code_generation: int = 0
    subject_hash: str = ""
    verification_fingerprint: str = ""
    failure_fingerprint: str = ""
    action_fingerprint: str = ""
    interaction_revision: int = 0
    reason: str = "initial task state"

    def __post_init__(self) -> None:
        for name in ("code_generation", "interaction_revision"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in (
            "subject_hash",
            "verification_fingerprint",
            "failure_fingerprint",
            "action_fingerprint",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) > 256:
                raise ValueError(f"{name} must be bounded text")
        if not isinstance(self.reason, str) or not self.reason.strip() or len(self.reason) > 256:
            raise ValueError("reason must be bounded non-blank text")

    @property
    def digest(self) -> str:
        payload = {
            "action": self.action_fingerprint,
            "failure": self.failure_fingerprint,
            "generation": self.code_generation,
            "interaction": self.interaction_revision,
            "subject": self.subject_hash if self.code_generation else "",
            "verification": self.verification_fingerprint,
        }
        encoded = json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


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
    input_tokens: int = 0
    output_tokens: int = 0
    repair_cycles: int = 0
    repeated_failures: int = 0
    last_failure_signature: str | None = None
    active_seconds: int = 0
    warned_at_80: bool = False
    warned_at_90: bool = False
    lease_tier: BudgetLeaseTier = BudgetLeaseTier.STANDARD
    lease_model_turn_limit: int | None = None
    lease_tool_call_limit: int | None = None
    lease_renewals: int = 0
    lease_final_extension: bool = False
    lease_progress_baseline: str = ""
    lease_last_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise ValueError("model_name must be non-blank text")
        if not isinstance(self.limits, EngineLimits):
            raise TypeError("limits must be EngineLimits")
        for name in ("model_turns", "tool_calls", "input_tokens", "output_tokens", "repair_cycles", "repeated_failures", "active_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.model_turns > self.limits.max_agent_rounds:
            raise ValueError("model_turns exceeds task limit")
        if self.tool_calls > self.limits.max_tool_calls:
            raise ValueError("tool_calls exceeds task limit")
        if self.input_tokens + self.output_tokens > self.limits.max_total_tokens:
            raise ValueError("token usage exceeds task limit")
        if self.last_failure_signature is not None and (not isinstance(self.last_failure_signature, str) or len(self.last_failure_signature) > 1024):
            raise ValueError("last_failure_signature must be bounded text or None")
        if not isinstance(self.warned_at_80, bool) or not isinstance(self.warned_at_90, bool):
            raise TypeError("budget warning flags must be booleans")
        if not isinstance(self.lease_tier, BudgetLeaseTier):
            raise TypeError("lease_tier must be a BudgetLeaseTier")
        default_turns, default_tools = lease_limits(self.lease_tier, self.limits)
        if self.lease_model_turn_limit is None:
            object.__setattr__(self, "lease_model_turn_limit", default_turns)
        if self.lease_tool_call_limit is None:
            object.__setattr__(self, "lease_tool_call_limit", default_tools)
        for name, maximum in (
            ("lease_model_turn_limit", self.limits.max_agent_rounds),
            ("lease_tool_call_limit", self.limits.max_tool_calls),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
                raise ValueError(f"{name} must be within the hard task limit")
        if (
            isinstance(self.lease_renewals, bool)
            or not isinstance(self.lease_renewals, int)
            or self.lease_renewals < 0
            or self.lease_renewals > 3
        ):
            raise ValueError("lease_renewals must be between zero and three")
        if not isinstance(self.lease_final_extension, bool):
            raise TypeError("lease_final_extension must be a boolean")
        if (
            not isinstance(self.lease_progress_baseline, str)
            or len(self.lease_progress_baseline) > 64
        ):
            raise ValueError("lease_progress_baseline must be a bounded digest")
        if self.lease_progress_baseline and not re.fullmatch(
            r"[0-9a-f]{64}", self.lease_progress_baseline
        ):
            raise ValueError("lease_progress_baseline must be a SHA-256 digest")
        if self.lease_last_reason is not None and (
            not isinstance(self.lease_last_reason, str)
            or not self.lease_last_reason.strip()
            or len(self.lease_last_reason) > 256
        ):
            raise ValueError("lease_last_reason must be bounded text or None")


@dataclass(frozen=True)
class BudgetReservation:
    budget: TaskBudget
    status: BudgetReserveStatus
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.budget, TaskBudget):
            raise TypeError("budget must be a TaskBudget")
        if not isinstance(self.status, BudgetReserveStatus):
            raise TypeError("status must be a BudgetReserveStatus")
        if self.reason is not None and (
            not isinstance(self.reason, str)
            or not self.reason.strip()
            or len(self.reason) > 256
        ):
            raise ValueError("reason must be bounded text or None")

    @property
    def accepted(self) -> bool:
        return self.status in {
            BudgetReserveStatus.RESERVED,
            BudgetReserveStatus.RENEWED,
        }


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
