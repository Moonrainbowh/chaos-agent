from __future__ import annotations

import sqlite3
from typing import Mapping, cast

from code_agent.core._json import JSONValue
from code_agent.core.limits import EngineLimits, TaskBudget

from . import _task_budget
from .errors import SessionCorruptionError


USAGE_NAMES = (
    "model_turns",
    "tool_calls",
    "input_tokens",
    "output_tokens",
    "repair_cycles",
    "repeated_failures",
    "active_seconds",
    "warned_at_80",
    "warned_at_90",
)


def copy_budget(
    connection: sqlite3.Connection,
    source_thread: str,
    target_thread: str,
    lineage_id: str,
    checkpoint_payload: Mapping[str, JSONValue],
) -> None:
    row = connection.execute(
        "SELECT * FROM task_budgets WHERE thread_id = ?", (source_thread,)
    ).fetchone()
    if row is None:
        raise SessionCorruptionError("source task budget is missing")
    current = _task_budget.task_budget(row)
    checkpoint = _budget_from_payload(checkpoint_payload, current)
    usage = connection.execute(
        "SELECT * FROM workspace_lineage_usage WHERE lineage_id = ?", (lineage_id,)
    ).fetchone()
    if usage is None:
        raise SessionCorruptionError("lineage budget usage is missing")
    values = _cumulative_budget(current, checkpoint, usage)
    _insert_budget(connection, target_thread, current, values)
    _update_lineage_usage(connection, lineage_id, values)


def _budget_from_payload(
    payload: Mapping[str, JSONValue], source: TaskBudget
) -> TaskBudget:
    try:
        limits = EngineLimits(
            _integer(payload, "max_agent_rounds", source.limits.max_agent_rounds),
            _integer(payload, "max_tool_calls", source.limits.max_tool_calls),
            _integer(
                payload,
                "max_tool_calls_per_round",
                source.limits.max_tool_calls_per_round,
            ),
            _integer(payload, "max_total_tokens", source.limits.max_total_tokens),
        )
        return TaskBudget(
            cast(str, payload.get("model_name", source.model_name)),
            limits,
            *(_integer(payload, name, 0) for name in USAGE_NAMES[:6]),
            cast(str | None, payload.get("last_failure_signature")),
            _integer(payload, "active_seconds", 0),
            _boolean(payload, "warned_at_80", False),
            _boolean(payload, "warned_at_90", False),
        )
    except (TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid checkpoint budget payload") from error


def _integer(
    payload: Mapping[str, JSONValue], name: str, default: int
) -> int:
    value = payload.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    return value


def _boolean(
    payload: Mapping[str, JSONValue], name: str, default: bool
) -> bool:
    value = payload.get(name, default)
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _cumulative_budget(
    current: TaskBudget, checkpoint: TaskBudget, usage: sqlite3.Row
) -> dict[str, int]:
    values: dict[str, int] = {}
    for name in USAGE_NAMES:
        values[name] = max(
            int(getattr(current, name)),
            int(getattr(checkpoint, name)),
            int(usage[name]),
        )
    return values


def _insert_budget(
    connection: sqlite3.Connection,
    thread_id: str,
    source: TaskBudget,
    values: Mapping[str, int],
) -> None:
    columns = (
        "thread_id, model_name, max_agent_rounds, max_tool_calls, "
        "max_tool_calls_per_round, max_total_tokens, "
        + ", ".join(USAGE_NAMES[:6])
        + ", last_failure_signature, "
        + ", ".join(USAGE_NAMES[6:])
    )
    arguments = (
        thread_id,
        source.model_name,
        source.limits.max_agent_rounds,
        source.limits.max_tool_calls,
        source.limits.max_tool_calls_per_round,
        source.limits.max_total_tokens,
        *(values[name] for name in USAGE_NAMES[:6]),
        source.last_failure_signature,
        *(values[name] for name in USAGE_NAMES[6:]),
    )
    placeholders = ", ".join("?" for _ in arguments)
    connection.execute(
        f"INSERT INTO task_budgets({columns}) VALUES ({placeholders})", arguments
    )


def _update_lineage_usage(
    connection: sqlite3.Connection, lineage_id: str, values: Mapping[str, int]
) -> None:
    assignments = ", ".join(f"{name} = ?" for name in USAGE_NAMES)
    connection.execute(
        f"UPDATE workspace_lineage_usage SET {assignments} WHERE lineage_id = ?",
        (*(values[name] for name in USAGE_NAMES), lineage_id),
    )
