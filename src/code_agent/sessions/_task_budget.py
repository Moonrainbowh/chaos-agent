from __future__ import annotations

import sqlite3

from code_agent.core.limits import EngineLimits, TaskBudget
from code_agent.core.models import Usage

from ._records import _require_thread, _text
from .errors import SessionCorruptionError


async def get_or_create(database: object, thread_id: str, model_name: str, limits: EngineLimits) -> TaskBudget:
    thread_id = _text(thread_id, "thread_id")
    model_name = _text(model_name, "model_name")
    if not isinstance(limits, EngineLimits):
        raise TypeError("limits must be EngineLimits")

    def write(connection: sqlite3.Connection) -> TaskBudget:
        _require_thread(connection, thread_id)
        row = connection.execute("SELECT * FROM task_budgets WHERE thread_id = ?", (thread_id,)).fetchone()
        if row is None:
            connection.execute("INSERT INTO task_budgets(thread_id, model_name, max_agent_rounds, max_tool_calls, max_tool_calls_per_round, max_total_tokens) VALUES (?, ?, ?, ?, ?, ?)", (thread_id, model_name, limits.max_agent_rounds, limits.max_tool_calls, limits.max_tool_calls_per_round, limits.max_total_tokens))
            return TaskBudget(model_name, limits)
        return task_budget(row)

    return await database.write(write)  # type: ignore[attr-defined]


async def reserve(database: object, thread_id: str, *, model_turns: int = 0, tool_calls: int = 0) -> TaskBudget | None:
    thread_id = _text(thread_id, "thread_id")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (model_turns, tool_calls)):
        raise ValueError("budget increments must be non-negative integers")

    def write(connection: sqlite3.Connection) -> TaskBudget | None:
        _require_thread(connection, thread_id)
        row = connection.execute("SELECT * FROM task_budgets WHERE thread_id = ?", (thread_id,)).fetchone()
        if row is None:
            raise SessionCorruptionError("task budget is missing")
        current = task_budget(row)
        if current.model_turns + model_turns > current.limits.max_agent_rounds or current.tool_calls + tool_calls > current.limits.max_tool_calls:
            return None
        updated = TaskBudget(current.model_name, current.limits, current.model_turns + model_turns, current.tool_calls + tool_calls, current.input_tokens, current.output_tokens, current.repair_cycles, current.repeated_failures, current.last_failure_signature, current.active_seconds, current.warned_at_80, current.warned_at_90)
        connection.execute("UPDATE task_budgets SET model_turns = ?, tool_calls = ? WHERE thread_id = ?", (updated.model_turns, updated.tool_calls, thread_id))
        return updated

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
        updated = TaskBudget(current.model_name, current.limits, current.model_turns, current.tool_calls, current.input_tokens + usage.input_tokens, current.output_tokens + usage.output_tokens, current.repair_cycles, current.repeated_failures, current.last_failure_signature, current.active_seconds, current.warned_at_80, current.warned_at_90)
        connection.execute("UPDATE task_budgets SET input_tokens = ?, output_tokens = ? WHERE thread_id = ?", (updated.input_tokens, updated.output_tokens, task.thread_id))
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
        updated = TaskBudget(current.model_name, current.limits, current.model_turns, current.tool_calls, current.input_tokens, current.output_tokens, repairs, repeated, fingerprint, current.active_seconds, current.warned_at_80, current.warned_at_90)
        connection.execute("UPDATE task_budgets SET repair_cycles = ?, repeated_failures = ?, last_failure_signature = ? WHERE thread_id = ?", (repairs, repeated, fingerprint, task.thread_id))
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
        return TaskBudget(current.model_name, current.limits, current.model_turns, current.tool_calls, current.input_tokens, current.output_tokens, current.repair_cycles, current.repeated_failures, current.last_failure_signature, active_seconds, current.warned_at_80, current.warned_at_90)

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
        return tuple(thresholds)

    return await database.write(write)  # type: ignore[attr-defined]


def task_budget(row: sqlite3.Row) -> TaskBudget:
    try:
        return TaskBudget(row["model_name"], EngineLimits(max_agent_rounds=row["max_agent_rounds"], max_tool_calls=row["max_tool_calls"], max_tool_calls_per_round=row["max_tool_calls_per_round"], max_total_tokens=row["max_total_tokens"]), row["model_turns"], row["tool_calls"], row["input_tokens"], row["output_tokens"], row["repair_cycles"], row["repeated_failures"], row["last_failure_signature"], row["active_seconds"], bool(row["warned_at_80"]), bool(row["warned_at_90"]))
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted task budget") from error
