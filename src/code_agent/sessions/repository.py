from __future__ import annotations

import sqlite3
import uuid
from typing import Optional

from code_agent.core.events import AgentEvent
from code_agent.core.models import ActionRequest, ActionResult, Message, Usage
from code_agent.core.task_state import TaskState, reduce_task_state
from code_agent.core.limits import EngineLimits, TaskBudget
from code_agent.core.task import TaskContract, TaskRecord, TaskStatus

from ._codec import (
    decode_datetime,
    decode_event,
    decode_message,
    decode_task_state,
    encode_datetime,
    encode_event,
    encode_message,
    encode_task_state,
    encode_task,
    decode_task,
    utc_now,
)
from ._database import SessionDatabase
from ._records import RecordRepositoryMixin, _require_thread, _text, _touch_thread
from .errors import SessionCorruptionError, SessionNotFound
from .models import ThreadStatus, ThreadSummary


class SQLiteSessionRepository(RecordRepositoryMixin):
    """Persist core sessions with one SQLite transaction per async operation."""

    def __init__(self, database_path: str | object) -> None:
        self._database = SessionDatabase(database_path)  # type: ignore[arg-type]

    async def create_thread(self, title: Optional[str] = None) -> str:
        if title is not None:
            title = _text(title, "title")
        identifier = uuid.uuid4().hex
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                "INSERT INTO threads(id, created_at, updated_at, title, status) VALUES (?, ?, ?, ?, ?)",
                (
                    identifier,
                    timestamp,
                    timestamp,
                    title,
                    ThreadStatus.ACTIVE.value,
                ),
            )

        await self._database.write(write)
        return identifier

    async def create_task(self, thread_id: str, contract: TaskContract) -> TaskRecord:
        if not isinstance(contract, TaskContract):
            raise TypeError("contract must be a TaskContract")
        record = TaskRecord.new(thread_id, contract.objective, contract.authorization, max_active_seconds=contract.max_active_seconds, max_repair_cycles=contract.max_repair_cycles, max_repeated_failure_signatures=contract.max_repeated_failure_signatures)
        def write(connection: sqlite3.Connection) -> TaskRecord:
            _require_thread(connection, record.thread_id)
            connection.execute("INSERT INTO tasks(id, thread_id, contract, status, stop_reason, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (record.id, record.thread_id, encode_task(record), record.status.value, record.stop_reason, encode_datetime(record.created_at), encode_datetime(record.updated_at)))
            return record
        return await self._database.write(write)

    async def load_task(self, task_id: str) -> TaskRecord:
        def read(connection: sqlite3.Connection) -> TaskRecord:
            row = connection.execute("SELECT contract, id, thread_id, status, stop_reason, created_at, updated_at FROM tasks WHERE id = ?", (_text(task_id, "task_id"),)).fetchone()
            if row is None: raise SessionNotFound("task not found")
            return _row_task(row)
        return await self._database.read(read)

    async def load_task_for_thread(self, thread_id: str) -> TaskRecord | None:
        def read(connection: sqlite3.Connection) -> TaskRecord | None:
            row = connection.execute("SELECT contract, id, thread_id, status, stop_reason, created_at, updated_at FROM tasks WHERE thread_id = ?", (_text(thread_id, "thread_id"),)).fetchone()
            return None if row is None else _row_task(row)
        return await self._database.read(read)

    async def transition_task(self, task_id: str, status: TaskStatus, reason: str | None = None) -> TaskRecord:
        def write(connection: sqlite3.Connection) -> TaskRecord:
            row = connection.execute("SELECT contract, id, thread_id, status, stop_reason, created_at, updated_at FROM tasks WHERE id = ?", (_text(task_id, "task_id"),)).fetchone()
            if row is None: raise SessionNotFound("task not found")
            updated = _row_task(row).transition(status, reason)
            connection.execute("UPDATE tasks SET contract = ?, status = ?, stop_reason = ?, updated_at = ? WHERE id = ?", (encode_task(updated), updated.status.value, updated.stop_reason, encode_datetime(updated.updated_at), updated.id))
            return updated
        return await self._database.write(write)

    async def list_tasks(self, *, include_terminal: bool = False) -> tuple[TaskRecord, ...]:
        def read(connection: sqlite3.Connection) -> tuple[TaskRecord, ...]:
            where = "" if include_terminal else "WHERE status NOT IN ('completed', 'failed')"
            return tuple(_row_task(row) for row in connection.execute(f"SELECT contract, id, thread_id, status, stop_reason, created_at, updated_at FROM tasks {where} ORDER BY updated_at DESC, id").fetchall())
        return await self._database.read(read)

    async def load_messages(self, thread_id: str) -> tuple[Message, ...]:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> tuple[Message, ...]:
            _require_thread(connection, thread_id)
            rows = connection.execute(
                "SELECT payload FROM messages WHERE thread_id = ? ORDER BY sequence",
                (thread_id,),
            ).fetchall()
            return tuple(decode_message(row[0]) for row in rows)

        return await self._database.read(read)

    async def append_message(self, thread_id: str, message: Message) -> None:
        thread_id = _text(thread_id, "thread_id")
        payload = encode_message(message)
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            _require_thread(connection, thread_id)
            connection.execute(
                "INSERT INTO messages(thread_id, payload, created_at) VALUES (?, ?, ?)",
                (thread_id, payload, timestamp),
            )
            _touch_thread(connection, thread_id, timestamp)

        await self._database.write(write)

    async def append_event(self, thread_id: str, event: AgentEvent) -> None:
        thread_id = _text(thread_id, "thread_id")
        payload = encode_event(event)
        timestamp = encode_datetime(event.timestamp)

        def write(connection: sqlite3.Connection) -> None:
            _require_thread(connection, thread_id)
            connection.execute(
                "INSERT INTO events(thread_id, payload, created_at) VALUES (?, ?, ?)",
                (thread_id, payload, timestamp),
            )
            _touch_thread(connection, thread_id, encode_datetime(utc_now()))

        await self._database.write(write)

    async def load_events(self, thread_id: str) -> tuple[AgentEvent, ...]:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> tuple[AgentEvent, ...]:
            _require_thread(connection, thread_id)
            rows = connection.execute(
                "SELECT payload FROM events WHERE thread_id = ? ORDER BY sequence",
                (thread_id,),
            ).fetchall()
            return tuple(decode_event(row[0]) for row in rows)

        return await self._database.read(read)

    async def load_task_state(self, thread_id: str) -> TaskState:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> TaskState:
            _require_thread(connection, thread_id)
            row = connection.execute(
                "SELECT payload FROM task_states WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            return TaskState.empty() if row is None else decode_task_state(row["payload"])

        return await self._database.read(read)

    async def save_task_state(self, thread_id: str, state: TaskState) -> None:
        thread_id = _text(thread_id, "thread_id")
        payload = encode_task_state(state)
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            _require_thread(connection, thread_id)
            connection.execute(
                "INSERT INTO task_states(thread_id, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
                (thread_id, payload, timestamp),
            )
            _touch_thread(connection, thread_id, timestamp)

        await self._database.write(write)

    async def reduce_task_state(
        self, thread_id: str, request: ActionRequest, result: ActionResult
    ) -> TaskState:
        thread_id = _text(thread_id, "thread_id")
        if not isinstance(request, ActionRequest) or not isinstance(result, ActionResult):
            raise TypeError("request and result must be action values")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> TaskState:
            _require_thread(connection, thread_id)
            row = connection.execute(
                "SELECT payload FROM task_states WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            current = TaskState.empty() if row is None else decode_task_state(row["payload"])
            updated = reduce_task_state(current, request, result)
            connection.execute(
                "INSERT INTO task_states(thread_id, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
                (thread_id, encode_task_state(updated), timestamp),
            )
            _touch_thread(connection, thread_id, timestamp)
            return updated

        return await self._database.write(write)

    async def get_or_create_task_budget(
        self, thread_id: str, model_name: str, limits: EngineLimits
    ) -> TaskBudget:
        thread_id = _text(thread_id, "thread_id")
        model_name = _text(model_name, "model_name")
        if not isinstance(limits, EngineLimits):
            raise TypeError("limits must be EngineLimits")

        def write(connection: sqlite3.Connection) -> TaskBudget:
            _require_thread(connection, thread_id)
            row = connection.execute(
                "SELECT * FROM task_budgets WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO task_budgets(thread_id, model_name, max_agent_rounds, max_tool_calls, max_tool_calls_per_round) VALUES (?, ?, ?, ?, ?)",
                    (thread_id, model_name, limits.max_agent_rounds, limits.max_tool_calls, limits.max_tool_calls_per_round),
                )
                return TaskBudget(model_name, limits)
            return _task_budget(row)

        return await self._database.write(write)

    async def reserve_task_budget(
        self, thread_id: str, *, model_turns: int = 0, tool_calls: int = 0
    ) -> TaskBudget | None:
        thread_id = _text(thread_id, "thread_id")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (model_turns, tool_calls)):
            raise ValueError("budget increments must be non-negative integers")

        def write(connection: sqlite3.Connection) -> TaskBudget | None:
            _require_thread(connection, thread_id)
            row = connection.execute(
                "SELECT * FROM task_budgets WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if row is None:
                raise SessionCorruptionError("task budget is missing")
            current = _task_budget(row)
            if (current.model_turns + model_turns > current.limits.max_agent_rounds or current.tool_calls + tool_calls > current.limits.max_tool_calls):
                return None
            next_budget = TaskBudget(
                current.model_name, current.limits,
                current.model_turns + model_turns, current.tool_calls + tool_calls,
            )
            connection.execute(
                "UPDATE task_budgets SET model_turns = ?, tool_calls = ? WHERE thread_id = ?",
                (next_budget.model_turns, next_budget.tool_calls, thread_id),
            )
            return next_budget

        return await self._database.write(write)

    async def load_task_budget(self, task_id: str) -> TaskBudget:
        task = await self.load_task(task_id)
        def read(connection: sqlite3.Connection) -> TaskBudget:
            row = connection.execute("SELECT * FROM task_budgets WHERE thread_id = ?", (task.thread_id,)).fetchone()
            if row is None: raise SessionCorruptionError("task budget is missing")
            return _task_budget(row)
        return await self._database.read(read)

    async def consume_task_usage(self, task_id: str, usage: Usage) -> TaskBudget:
        if not isinstance(usage, Usage):
            raise TypeError("usage must be a Usage")
        task = await self.load_task(task_id)
        def write(connection: sqlite3.Connection) -> TaskBudget:
            row = connection.execute("SELECT * FROM task_budgets WHERE thread_id = ?", (task.thread_id,)).fetchone()
            if row is None: raise SessionCorruptionError("task budget is missing")
            current = _task_budget(row)
            updated = TaskBudget(current.model_name, current.limits, current.model_turns, current.tool_calls, current.input_tokens + usage.input_tokens, current.output_tokens + usage.output_tokens, current.repair_cycles, current.repeated_failures, current.last_failure_signature, current.active_seconds)
            connection.execute("UPDATE task_budgets SET input_tokens = ?, output_tokens = ? WHERE thread_id = ?", (updated.input_tokens, updated.output_tokens, task.thread_id))
            return updated
        return await self._database.write(write)

    async def observe_task_validation(self, task_id: str, fingerprint: str | None, changed_files: int) -> TaskBudget:
        if fingerprint is not None and (not isinstance(fingerprint, str) or len(fingerprint) > 1024): raise ValueError("fingerprint must be bounded text or None")
        if isinstance(changed_files, bool) or not isinstance(changed_files, int) or changed_files < 0: raise ValueError("changed_files must be non-negative")
        task = await self.load_task(task_id)
        def write(connection: sqlite3.Connection) -> TaskBudget:
            row = connection.execute("SELECT * FROM task_budgets WHERE thread_id = ?", (task.thread_id,)).fetchone()
            if row is None: raise SessionCorruptionError("task budget is missing")
            current = _task_budget(row)
            repeated = current.repeated_failures + 1 if fingerprint and fingerprint == current.last_failure_signature and changed_files else 1 if fingerprint and changed_files else 0
            repairs = current.repair_cycles + (1 if fingerprint and changed_files else 0)
            updated = TaskBudget(current.model_name, current.limits, current.model_turns, current.tool_calls, current.input_tokens, current.output_tokens, repairs, repeated, fingerprint, current.active_seconds)
            connection.execute("UPDATE task_budgets SET repair_cycles = ?, repeated_failures = ?, last_failure_signature = ? WHERE thread_id = ?", (repairs, repeated, fingerprint, task.thread_id))
            return updated
        return await self._database.write(write)

    async def record_task_active_seconds(self, task_id: str, active_seconds: int) -> TaskBudget:
        if isinstance(active_seconds, bool) or not isinstance(active_seconds, int) or active_seconds < 0:
            raise ValueError("active_seconds must be a non-negative integer")
        task = await self.load_task(task_id)

        def write(connection: sqlite3.Connection) -> TaskBudget:
            row = connection.execute("SELECT * FROM task_budgets WHERE thread_id = ?", (task.thread_id,)).fetchone()
            if row is None:
                raise SessionCorruptionError("task budget is missing")
            current = _task_budget(row)
            if active_seconds < current.active_seconds:
                raise ValueError("active_seconds must not decrease")
            connection.execute(
                "UPDATE task_budgets SET active_seconds = ? WHERE thread_id = ?",
                (active_seconds, task.thread_id),
            )
            return TaskBudget(
                current.model_name, current.limits, current.model_turns,
                current.tool_calls, current.input_tokens, current.output_tokens,
                current.repair_cycles, current.repeated_failures,
                current.last_failure_signature, active_seconds,
            )

        return await self._database.write(write)

    async def record_task_control(self, task_id: str, instruction: str) -> None:
        task_id = _text(task_id, "task_id")
        instruction = _text(instruction, "instruction")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            row = connection.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise SessionNotFound("task not found")
            connection.execute(
                "INSERT INTO task_controls(task_id, instruction, created_at) VALUES (?, ?, ?)",
                (task_id, instruction, timestamp),
            )

        await self._database.write(write)

    async def consume_task_controls(self, task_id: str) -> tuple[str, ...]:
        task_id = _text(task_id, "task_id")

        def write(connection: sqlite3.Connection) -> tuple[str, ...]:
            row = connection.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise SessionNotFound("task not found")
            rows = connection.execute(
                "SELECT sequence, instruction FROM task_controls WHERE task_id = ? ORDER BY sequence",
                (task_id,),
            ).fetchall()
            instructions = tuple(_text(row["instruction"], "instruction") for row in rows)
            if rows:
                connection.execute("DELETE FROM task_controls WHERE task_id = ?", (task_id,))
            return instructions

        return await self._database.write(write)

    async def archive_thread(self, thread_id: str) -> None:
        thread_id = _text(thread_id, "thread_id")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            cursor = connection.execute(
                "UPDATE threads SET status = ?, updated_at = ? WHERE id = ?",
                (ThreadStatus.ARCHIVED.value, timestamp, thread_id),
            )
            if cursor.rowcount != 1:
                raise SessionNotFound(f"thread not found: {thread_id}")

        await self._database.write(write)

    async def list_threads(
        self, *, limit: int = 100, include_archived: bool = False
    ) -> tuple[ThreadSummary, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit <= 0 or limit > 1_000:
            raise ValueError("limit must be between 1 and 1000")
        if not isinstance(include_archived, bool):
            raise TypeError("include_archived must be a bool")

        def read(connection: sqlite3.Connection) -> tuple[ThreadSummary, ...]:
            where = "" if include_archived else "WHERE t.status = 'active'"
            rows = connection.execute(
                f"""SELECT t.*,
                    (SELECT COUNT(*) FROM messages m WHERE m.thread_id = t.id) AS message_count,
                    (SELECT payload FROM messages m WHERE m.thread_id = t.id ORDER BY sequence DESC LIMIT 1) AS last_payload
                    FROM threads t {where}
                    ORDER BY t.updated_at DESC, t.id ASC LIMIT ?""",
                (limit,),
            ).fetchall()
            try:
                return tuple(_summary(row) for row in rows)
            except (TypeError, ValueError) as error:
                raise SessionCorruptionError("invalid persisted thread") from error

        return await self._database.read(read)


def _summary(row: sqlite3.Row) -> ThreadSummary:
    last_payload = row["last_payload"]
    preview = None
    if last_payload is not None:
        preview = " ".join(decode_message(last_payload).content.split())[:120]
    return ThreadSummary(
        id=row["id"],
        title=row["title"],
        status=ThreadStatus(row["status"]),
        created_at=decode_datetime(row["created_at"], "thread"),
        updated_at=decode_datetime(row["updated_at"], "thread"),
        message_count=row["message_count"],
        last_message_preview=preview,
    )


def _task_budget(row: sqlite3.Row) -> TaskBudget:
    try:
        return TaskBudget(
            row["model_name"],
            EngineLimits(
                max_agent_rounds=row["max_agent_rounds"],
                max_tool_calls=row["max_tool_calls"],
                max_tool_calls_per_round=row["max_tool_calls_per_round"],
            ),
            row["model_turns"],
            row["tool_calls"], row["input_tokens"], row["output_tokens"],
            row["repair_cycles"], row["repeated_failures"], row["last_failure_signature"],
            row["active_seconds"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted task budget") from error


def _row_task(row: sqlite3.Row) -> TaskRecord:
    try:
        return TaskRecord.from_dict({
            "id": row["id"], "thread_id": row["thread_id"],
            "contract": __import__("json").loads(row["contract"])["contract"],
            "status": row["status"], "stop_reason": row["stop_reason"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted task") from error
