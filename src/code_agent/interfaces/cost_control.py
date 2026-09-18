from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CostReport:
    task_id: str
    model: str
    input_tokens: int
    output_tokens: int
    model_turns: int
    tool_calls: int
    lease_tier: str
    lease_model_turn_limit: int
    lease_tool_call_limit: int
    hard_model_turn_limit: int
    hard_tool_call_limit: int
    lease_renewals: int
    lease_final_extension: bool
    input_cost: Decimal | None
    output_cost: Decimal | None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_cost(self) -> Decimal | None:
        if self.input_cost is None or self.output_cost is None:
            return None
        return self.input_cost + self.output_cost


class TaskCostControl:
    """Read durable task usage and apply explicitly configured profile rates."""

    def __init__(self, sessions: object, profiles: dict[str, object]) -> None:
        self._sessions = sessions
        self._profiles = dict(profiles)

    async def report(
        self, *, task_id: str | None, thread_id: str | None
    ) -> CostReport:
        task = None
        if task_id:
            task = await self._sessions.load_task(task_id)
        elif thread_id:
            task = await self._sessions.load_task_for_thread(thread_id)
        if task is None:
            raise RuntimeError("no durable task usage is available for this thread")
        budget = await self._sessions.load_task_budget(task.id)
        profile = self._profiles.get(task.contract.profile_id or "")
        input_rate = getattr(profile, "input_cost_per_million", None)
        output_rate = getattr(profile, "output_cost_per_million", None)
        input_cost = _cost(budget.input_tokens, input_rate)
        output_cost = _cost(budget.output_tokens, output_rate)
        return CostReport(
            task_id=task.id,
            model=budget.model_name,
            input_tokens=budget.input_tokens,
            output_tokens=budget.output_tokens,
            model_turns=budget.model_turns,
            tool_calls=budget.tool_calls,
            lease_tier=budget.lease_tier.value,
            lease_model_turn_limit=budget.lease_model_turn_limit,
            lease_tool_call_limit=budget.lease_tool_call_limit,
            hard_model_turn_limit=budget.limits.max_agent_rounds,
            hard_tool_call_limit=budget.limits.max_tool_calls,
            lease_renewals=budget.lease_renewals,
            lease_final_extension=budget.lease_final_extension,
            input_cost=input_cost,
            output_cost=output_cost,
        )


def _cost(tokens: int, rate: object) -> Decimal | None:
    if rate is None:
        return None
    return Decimal(tokens) * Decimal(str(rate)) / Decimal(1_000_000)


def format_cost_report(report: CostReport) -> str:
    lines = [
        f"Task: {report.task_id}",
        f"Model: {report.model}",
        f"Prompt tokens: {report.input_tokens:,}",
        f"Completion tokens: {report.output_tokens:,}",
        f"Total tokens: {report.total_tokens:,}",
        (
            f"Soft lease ({report.lease_tier}): "
            f"model turns {report.model_turns}/{report.lease_model_turn_limit}, "
            f"tool calls {report.tool_calls}/{report.lease_tool_call_limit}"
        ),
        (
            f"Hard limit: model turns {report.model_turns}/"
            f"{report.hard_model_turn_limit}, tool calls {report.tool_calls}/"
            f"{report.hard_tool_call_limit}"
        ),
        (
            f"Lease renewals: {report.lease_renewals}; final extension: "
            f"{'active' if report.lease_final_extension else 'not used'}"
        ),
    ]
    if report.total_cost is None:
        lines.append("Cost: unavailable (profile pricing is not configured)")
    else:
        lines.extend(
            (
                f"Prompt cost: ${report.input_cost:.6f}",
                f"Completion cost: ${report.output_cost:.6f}",
                f"Estimated total: ${report.total_cost:.6f}",
            )
        )
    return "\n".join(lines)
