from __future__ import annotations

import sqlite3
import uuid
from typing import Optional

from code_agent.core.events import AgentEvent
from code_agent.core.models import ActionRequest, ActionResult, Message
from code_agent.core.task_state import TaskState, reduce_task_state
from code_agent.core.limits import EngineLimits, TaskBudget

from ._codec import (
    decode_datetime,
    decode_event,
    decode_message,
    decode_task_state,
    encode_datetime,
    encode_event,
    encode_message,
    encode_task_state,
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
                    "INSERT INTO task_budgets VALUES (?, ?, ?, ?, ?, 0, 0)",
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
            row["tool_calls"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted task budget") from error
