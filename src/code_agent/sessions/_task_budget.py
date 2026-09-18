from __future__ import annotations

import sqlite3
from dataclasses import replace

from code_agent.core.limits import (
    BudgetLeaseTier,
    BudgetReservation,
    BudgetReserveStatus,
    EngineLimits,
    TaskBudget,
    TaskProgressSnapshot,
    lease_limits,
)
from code_agent.core.models import Usage

from ._records import _require_thread, _text
from .errors import SessionCorruptionError


async def get_or_create(
    database: object,
    thread_id: str,
    model_name: str,
    limits: EngineLimits,
    lease_tier: BudgetLeaseTier | None = None,
) -> TaskBudget:
    thread_id = _text(thread_id, "thread_id")
    model_name = _text(model_name, "model_name")
    if not isinstance(limits, EngineLimits):
        raise TypeError("limits must be EngineLimits")
    if lease_tier is not None and not isinstance(lease_tier, BudgetLeaseTier):
        raise TypeError("lease_tier must be a BudgetLeaseTier or None")

    def write(connection: sqlite3.Connection) -> TaskBudget:
        _require_thread(connection, thread_id)
        row = connection.execute("SELECT * FROM task_budgets WHERE thread_id = ?", (thread_id,)).fetchone()
        if row is None:
            selected = lease_tier or BudgetLeaseTier.DEEP
            turns, tools = (
                lease_limits(selected, limits)
                if lease_tier is not None
                else (limits.max_agent_rounds, limits.max_tool_calls)
            )
            final_extension = lease_tier is None
            connection.execute(
                "INSERT INTO task_budgets("
                "thread_id, model_name, max_agent_rounds, max_tool_calls, "
                "max_tool_calls_per_round, max_total_tokens, lease_tier, "
                "lease_model_turn_limit, lease_tool_call_limit, lease_final_extension"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    model_name,
                    limits.max_agent_rounds,
                    limits.max_tool_calls,
                    limits.max_tool_calls_per_round,
                    limits.max_total_tokens,
                    selected.value,
                    turns,
                    tools,
                    int(final_extension),
                ),
            )
            result = TaskBudget(
                model_name,
                limits,
                lease_tier=selected,
                lease_model_turn_limit=turns,
                lease_tool_call_limit=tools,
                lease_final_extension=final_extension,
            )
        else:
            result = task_budget(row)
        sync_lineage_usage(connection, thread_id, result)
        return result

    return await database.write(write)  # type: ignore[attr-defined]


async def reserve(
    database: object,
    thread_id: str,
    *,
    model_turns: int = 0,
    tool_calls: int = 0,
    progress: TaskProgressSnapshot | None = None,
) -> BudgetReservation:
    thread_id = _text(thread_id, "thread_id")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (model_turns, tool_calls)):
        raise ValueError("budget increments must be non-negative integers")
    if progress is not None and not isinstance(progress, TaskProgressSnapshot):
        raise TypeError("progress must be a TaskProgressSnapshot or None")
    snapshot = progress or TaskProgressSnapshot()

    def write(connection: sqlite3.Connection) -> BudgetReservation:
        _require_thread(connection, thread_id)
        row = connection.execute("SELECT * FROM task_budgets WHERE thread_id = ?", (thread_id,)).fetchone()
        if row is None:
            raise SessionCorruptionError("task budget is missing")
        current = task_budget(row)
        if current.model_turns + model_turns > current.limits.max_agent_rounds or current.tool_calls + tool_calls > current.limits.max_tool_calls:
            return BudgetReservation(
                current,
                BudgetReserveStatus.HARD_EXHAUSTED,
                "hard task budget exhausted",
            )
        baseline = current.lease_progress_baseline or snapshot.digest
        lease_exceeded = (
            current.model_turns + model_turns > current.lease_model_turn_limit
            or current.tool_calls + tool_calls > current.lease_tool_call_limit
        )
        status = BudgetReserveStatus.RESERVED
        reason = None
        renewed = replace(current, lease_progress_baseline=baseline)
        if lease_exceeded:
            if snapshot.digest == baseline:
                return BudgetReservation(
                    renewed,
                    BudgetReserveStatus.LEASE_EXHAUSTED,
                    "soft lease exhausted without new trusted progress",
                )
            renewed = _renew_lease(renewed, snapshot)
            if renewed is None:
                return BudgetReservation(
                    current,
                    BudgetReserveStatus.LEASE_EXHAUSTED,
                    "soft lease exhausted after final extension",
                )
            if (
                current.model_turns + model_turns > renewed.lease_model_turn_limit
                or current.tool_calls + tool_calls > renewed.lease_tool_call_limit
            ):
                return BudgetReservation(
                    current,
                    BudgetReserveStatus.LEASE_EXHAUSTED,
                    "requested reservation exceeds renewed soft lease",
                )
            status = BudgetReserveStatus.RENEWED
            reason = snapshot.reason
        updated = replace(
            renewed,
            model_turns=current.model_turns + model_turns,
            tool_calls=current.tool_calls + tool_calls,
        )
        connection.execute(
            "UPDATE task_budgets SET model_turns = ?, tool_calls = ?, "
            "lease_tier = ?, lease_model_turn_limit = ?, lease_tool_call_limit = ?, "
            "lease_renewals = ?, lease_final_extension = ?, "
            "lease_progress_baseline = ?, lease_last_reason = ? WHERE thread_id = ?",
            (
                updated.model_turns,
                updated.tool_calls,
                updated.lease_tier.value,
                updated.lease_model_turn_limit,
                updated.lease_tool_call_limit,
                updated.lease_renewals,
                int(updated.lease_final_extension),
                updated.lease_progress_baseline,
                updated.lease_last_reason,
                thread_id,
            ),
        )
        sync_lineage_usage(connection, thread_id, updated)
        return BudgetReservation(updated, status, reason)

    return await database.write(write)  # type: ignore[attr-defined]


async def load(database: object, task_id: str, load_task: object) -> TaskBudget:
    task = await load_task(task_id)  # type: ignore[operator]

    def read(connection: sqlite3.Connection) -> TaskBudget:
        row = connection.execute("SELECT * FROM task_budgets WHERE thread_id = ?", (task.thread_id,)).fetchone()
        if row is None:
            raise SessionCorruptionError("task budget is missing")
        return task_budget(row)

    return await database.read(read)  # type: ignore[attr-defined]


async def consume_usage(database: object, task_id: str, usage: Usage, load_task: object) -> TaskBudget:
    if not isinstance(usage, Usage):
        raise TypeError("usage must be a Usage")
    task = await load_task(task_id)  # type: ignore[operator]

    def write(connection: sqlite3.Connection) -> TaskBudget:
        row = connection.execute("SELECT * FROM task_budgets WHERE thread_id = ?", (task.thread_id,)).fetchone()
        if row is None:
            raise SessionCorruptionError("task budget is missing")
        current = task_budget(row)
        updated = replace(current, input_tokens=current.input_tokens + usage.input_tokens, output_tokens=current.output_tokens + usage.output_tokens)
        connection.execute("UPDATE task_budgets SET input_tokens = ?, output_tokens = ? WHERE thread_id = ?", (updated.input_tokens, updated.output_tokens, task.thread_id))
        sync_lineage_usage(connection, task.thread_id, updated)
        return updated

    return await database.write(write)  # type: ignore[attr-defined]


async def observe_validation(database: object, task_id: str, fingerprint: str | None, changed_files: int, load_task: object) -> TaskBudget:
    if fingerprint is not None and (not isinstance(fingerprint, str) or len(fingerprint) > 1024):
        raise ValueError("fingerprint must be bounded text or None")
    if isinstance(changed_files, bool) or not isinstance(changed_files, int) or changed_files < 0:
        raise ValueError("changed_files must be non-negative")
    task = await load_task(task_id)  # type: ignore[operator]

    def write(connection: sqlite3.Connection) -> TaskBudget:
        row = connection.execute("SELECT * FROM task_budgets WHERE thread_id = ?", (task.thread_id,)).fetchone()
        if row is None:
            raise SessionCorruptionError("task budget is missing")
        current = task_budget(row)
        repeated = current.repeated_failures + 1 if fingerprint and fingerprint == current.last_failure_signature and changed_files else 1 if fingerprint and changed_files else 0
        repairs = current.repair_cycles + (1 if fingerprint and changed_files else 0)
        updated = replace(current, repair_cycles=repairs, repeated_failures=repeated, last_failure_signature=fingerprint)
        connection.execute("UPDATE task_budgets SET repair_cycles = ?, repeated_failures = ?, last_failure_signature = ? WHERE thread_id = ?", (repairs, repeated, fingerprint, task.thread_id))
        sync_lineage_usage(connection, task.thread_id, updated)
        return updated

    return await database.write(write)  # type: ignore[attr-defined]


async def record_active_seconds(database: object, task_id: str, active_seconds: int, load_task: object) -> TaskBudget:
    if isinstance(active_seconds, bool) or not isinstance(active_seconds, int) or active_seconds < 0:
        raise ValueError("active_seconds must be a non-negative integer")
    task = await load_task(task_id)  # type: ignore[operator]

    def write(connection: sqlite3.Connection) -> TaskBudget:
        row = connection.execute("SELECT * FROM task_budgets WHERE thread_id = ?", (task.thread_id,)).fetchone()
        if row is None:
            raise SessionCorruptionError("task budget is missing")
        current = task_budget(row)
        if active_seconds < current.active_seconds:
            raise ValueError("active_seconds must not decrease")
        connection.execute("UPDATE task_budgets SET active_seconds = ? WHERE thread_id = ?", (active_seconds, task.thread_id))
        updated = replace(current, active_seconds=active_seconds)
        sync_lineage_usage(connection, task.thread_id, updated)
        return updated

    return await database.write(write)  # type: ignore[attr-defined]


async def mark_warnings(database: object, task_id: str, load_task: object) -> tuple[int, ...]:
    task = await load_task(task_id)  # type: ignore[operator]

    def write(connection: sqlite3.Connection) -> tuple[int, ...]:
        row = connection.execute("SELECT * FROM task_budgets WHERE thread_id = ?", (task.thread_id,)).fetchone()
        if row is None:
            raise SessionCorruptionError("task budget is missing")
        budget = task_budget(row)
        usage = budget.input_tokens + budget.output_tokens
        thresholds: list[int] = []
        if usage * 100 >= budget.limits.max_total_tokens * 80 and not budget.warned_at_80:
            connection.execute("UPDATE task_budgets SET warned_at_80 = 1 WHERE thread_id = ?", (task.thread_id,))
            thresholds.append(80)
        if usage * 100 >= budget.limits.max_total_tokens * 90 and not budget.warned_at_90:
            connection.execute("UPDATE task_budgets SET warned_at_90 = 1 WHERE thread_id = ?", (task.thread_id,))
            thresholds.append(90)
        if thresholds:
            updated = connection.execute(
                "SELECT * FROM task_budgets WHERE thread_id = ?", (task.thread_id,)
            ).fetchone()
            sync_lineage_usage(connection, task.thread_id, task_budget(updated))
        return tuple(thresholds)

    return await database.write(write)  # type: ignore[attr-defined]


def task_budget(row: sqlite3.Row) -> TaskBudget:
    try:
        return TaskBudget(
            model_name=row["model_name"],
            limits=EngineLimits(
                max_agent_rounds=row["max_agent_rounds"],
                max_tool_calls=row["max_tool_calls"],
                max_tool_calls_per_round=row["max_tool_calls_per_round"],
                max_total_tokens=row["max_total_tokens"],
            ),
            model_turns=row["model_turns"],
            tool_calls=row["tool_calls"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            repair_cycles=row["repair_cycles"],
            repeated_failures=row["repeated_failures"],
            last_failure_signature=row["last_failure_signature"],
            active_seconds=row["active_seconds"],
            warned_at_80=bool(row["warned_at_80"]),
            warned_at_90=bool(row["warned_at_90"]),
            lease_tier=BudgetLeaseTier(row["lease_tier"]),
            lease_model_turn_limit=row["lease_model_turn_limit"],
            lease_tool_call_limit=row["lease_tool_call_limit"],
            lease_renewals=row["lease_renewals"],
            lease_final_extension=bool(row["lease_final_extension"]),
            lease_progress_baseline=row["lease_progress_baseline"],
            lease_last_reason=row["lease_last_reason"],
        )
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted task budget") from error


def _renew_lease(
    budget: TaskBudget, progress: TaskProgressSnapshot
) -> TaskBudget | None:
    if budget.lease_tier is BudgetLeaseTier.QUICK:
        tier = BudgetLeaseTier.STANDARD
        turns, tools = lease_limits(tier, budget.limits)
        final_extension = False
    elif budget.lease_tier is BudgetLeaseTier.STANDARD:
        tier = BudgetLeaseTier.DEEP
        turns, tools = lease_limits(tier, budget.limits)
        final_extension = False
    elif not budget.lease_final_extension:
        tier = BudgetLeaseTier.DEEP
        turns, tools = budget.limits.max_agent_rounds, budget.limits.max_tool_calls
        final_extension = True
    else:
        return None
    return replace(
        budget,
        lease_tier=tier,
        lease_model_turn_limit=max(budget.lease_model_turn_limit, turns),
        lease_tool_call_limit=max(budget.lease_tool_call_limit, tools),
        lease_renewals=budget.lease_renewals + 1,
        lease_final_extension=final_extension,
        lease_progress_baseline=progress.digest,
        lease_last_reason=progress.reason,
    )


def sync_lineage_usage(
    connection: sqlite3.Connection, thread_id: str, budget: TaskBudget
) -> None:
    row = connection.execute(
        "SELECT t.id AS task_id, t.workspace_lineage_id, l.owner_task_id, l.status "
        "FROM tasks t LEFT JOIN workspace_lineages l "
        "ON l.id = t.workspace_lineage_id WHERE t.thread_id = ?",
        (thread_id,),
    ).fetchone()
    if row is None or row["workspace_lineage_id"] is None:
        return
    if row["status"] is None or row["owner_task_id"] is None:
        raise SessionCorruptionError("task workspace lineage is missing")
    if row["status"] != "active":
        raise ValueError("task workspace lineage is not active")
    if row["owner_task_id"] != row["task_id"]:
        raise ValueError("task does not own its workspace lineage")
    values = (
        budget.model_turns,
        budget.tool_calls,
        budget.input_tokens,
        budget.output_tokens,
        budget.repair_cycles,
        budget.repeated_failures,
        budget.last_failure_signature,
        budget.active_seconds,
        int(budget.warned_at_80),
        int(budget.warned_at_90),
        row["workspace_lineage_id"],
    )
    connection.execute(
        "UPDATE workspace_lineage_usage SET "
        "model_turns = max(model_turns, ?), tool_calls = max(tool_calls, ?), "
        "input_tokens = max(input_tokens, ?), output_tokens = max(output_tokens, ?), "
        "repair_cycles = max(repair_cycles, ?), "
        "repeated_failures = ?, last_failure_signature = ?, "
        "active_seconds = max(active_seconds, ?), "
        "warned_at_80 = max(warned_at_80, ?), warned_at_90 = max(warned_at_90, ?) "
        "WHERE lineage_id = ?",
        values,
    )
