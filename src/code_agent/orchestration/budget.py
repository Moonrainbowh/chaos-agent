from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from .models import AgentUsage, ChildRunRequest


class BudgetExceededError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParentBudget:
    max_children: int = 8
    max_depth: int = 2
    max_concurrency: int = 4
    max_total_tokens: int = 200_000
    max_tool_calls: int = 128
    max_active_seconds: int = 3_600

    def __post_init__(self) -> None:
        for name in (
            "max_children",
            "max_depth",
            "max_concurrency",
            "max_total_tokens",
            "max_active_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(self.max_tool_calls, bool) or not isinstance(self.max_tool_calls, int) or self.max_tool_calls < 0:
            raise ValueError("max_tool_calls must be a non-negative integer")


@dataclass(frozen=True)
class BudgetLease:
    lease_id: str
    run_id: str
    token_budget: int
    tool_budget: int
    active_seconds: int


@dataclass(frozen=True)
class BudgetState:
    children_started: int
    running_leases: int
    used: AgentUsage
    reserved: AgentUsage


class BudgetLedger:
    def __init__(self, budget: ParentBudget) -> None:
        if not isinstance(budget, ParentBudget):
            raise TypeError("budget must be a ParentBudget")
        self._budget = budget
        self._children_started = 0
        self._used = AgentUsage()
        self._leases: dict[str, BudgetLease] = {}
        self._lock = asyncio.Lock()

    @property
    def budget(self) -> ParentBudget:
        return self._budget

    async def reserve(self, request: ChildRunRequest) -> BudgetLease:
        if not isinstance(request, ChildRunRequest):
            raise TypeError("request must be a ChildRunRequest")
        async with self._lock:
            if request.depth > self._budget.max_depth:
                raise BudgetExceededError("child depth exceeds parent budget")
            if self._children_started >= self._budget.max_children:
                raise BudgetExceededError("child count exceeds parent budget")
            reserved = self._reserved()
            remaining_tokens = self._budget.max_total_tokens - self._used.total_tokens - reserved.total_tokens
            remaining_tools = self._budget.max_tool_calls - self._used.tool_calls - reserved.tool_calls
            remaining_seconds = self._budget.max_active_seconds - self._used.active_seconds - reserved.active_seconds
            if request.token_budget > remaining_tokens:
                raise BudgetExceededError("child token reservation exceeds parent budget")
            if request.tool_budget > remaining_tools:
                raise BudgetExceededError("child tool reservation exceeds parent budget")
            if request.active_seconds > remaining_seconds:
                raise BudgetExceededError("child active-time reservation exceeds parent budget")
            lease = BudgetLease(
                uuid.uuid4().hex,
                request.run_id,
                request.token_budget,
                request.tool_budget,
                request.active_seconds,
            )
            self._leases[lease.lease_id] = lease
            self._children_started += 1
            return lease

    async def settle(self, lease: BudgetLease, usage: AgentUsage) -> BudgetState:
        if not isinstance(lease, BudgetLease) or not isinstance(usage, AgentUsage):
            raise TypeError("lease and usage must be typed values")
        async with self._lock:
            active = self._leases.pop(lease.lease_id, None)
            if active != lease:
                raise ValueError("budget lease is not active")
            exceeded = (
                usage.total_tokens > lease.token_budget
                or usage.tool_calls > lease.tool_budget
                or usage.active_seconds > lease.active_seconds
            )
            charged = AgentUsage(
                min(usage.total_tokens, lease.token_budget),
                min(usage.tool_calls, lease.tool_budget),
                min(usage.active_seconds, lease.active_seconds),
            )
            self._used = _add(self._used, charged)
            state = self._state()
            if exceeded:
                raise BudgetExceededError("child usage exceeded its reserved budget")
            return state

    async def release(self, lease: BudgetLease) -> BudgetState:
        return await self.settle(lease, AgentUsage())

    async def state(self) -> BudgetState:
        async with self._lock:
            return self._state()

    def _state(self) -> BudgetState:
        return BudgetState(
            self._children_started,
            len(self._leases),
            self._used,
            self._reserved(),
        )

    def _reserved(self) -> AgentUsage:
        return AgentUsage(
            sum(lease.token_budget for lease in self._leases.values()),
            sum(lease.tool_budget for lease in self._leases.values()),
            sum(lease.active_seconds for lease in self._leases.values()),
        )


def _add(left: AgentUsage, right: AgentUsage) -> AgentUsage:
    return AgentUsage(
        left.total_tokens + right.total_tokens,
        left.tool_calls + right.tool_calls,
        left.active_seconds + right.active_seconds,
    )
