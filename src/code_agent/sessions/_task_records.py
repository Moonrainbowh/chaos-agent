from __future__ import annotations

import json
import sqlite3
import uuid

from code_agent.core.models import ActionRequest, ActionResult
from code_agent.core.task import TaskContract, TaskRecord, TaskStatus
from code_agent.core.task_state import TaskState, reduce_task_state

from ._codec import (
    decode_task_state,
    encode_datetime,
    encode_task,
    encode_task_state,
    utc_now,
)
from ._records import _require_thread, _text, _touch_thread
from .errors import SessionCorruptionError, SessionNotFound


class TaskRecordRepositoryMixin:
    _database: object

    async def create_task(self, thread_id: str, contract: TaskContract) -> TaskRecord:
        if not isinstance(contract, TaskContract):
            raise TypeError("contract must be a TaskContract")
        record = TaskRecord(uuid.uuid4().hex, thread_id, contract)

        def write(connection: sqlite3.Connection) -> TaskRecord:
            _require_thread(connection, record.thread_id)
            connection.execute(
                "INSERT INTO tasks(id, thread_id, contract, status, stop_reason, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.thread_id,
                    encode_task(record),
                    record.status.value,
                    record.stop_reason,
                    encode_datetime(record.created_at),
                    encode_datetime(record.updated_at),
                ),
            )
            return record

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def load_task(self, task_id: str) -> TaskRecord:
        task_id = _text(task_id, "task_id")

        def read(connection: sqlite3.Connection) -> TaskRecord:
            row = _task_row(connection, "id", task_id)
            if row is None:
                raise SessionNotFound("task not found")
            return row_task(row)

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def load_task_for_thread(self, thread_id: str) -> TaskRecord | None:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> TaskRecord | None:
            row = _task_row(connection, "thread_id", thread_id)
            return None if row is None else row_task(row)

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def transition_task(
        self, task_id: str, status: TaskStatus, reason: str | None = None
    ) -> TaskRecord:
        task_id = _text(task_id, "task_id")

        def write(connection: sqlite3.Connection) -> TaskRecord:
            row = _task_row(connection, "id", task_id)
            if row is None:
                raise SessionNotFound("task not found")
            updated = row_task(row).transition(status, reason)
            connection.execute(
                "UPDATE tasks SET contract = ?, status = ?, stop_reason = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    encode_task(updated),
                    updated.status.value,
                    updated.stop_reason,
                    encode_datetime(updated.updated_at),
                    updated.id,
                ),
            )
            return updated

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def list_tasks(
        self, *, include_terminal: bool = False
    ) -> tuple[TaskRecord, ...]:
        def read(connection: sqlite3.Connection) -> tuple[TaskRecord, ...]:
            where = (
                ""
                if include_terminal
                else "WHERE status NOT IN "
                "('completed', 'accepted_partial', 'failed', 'superseded')"
            )
            rows = connection.execute(
                f"SELECT contract, id, thread_id, status, stop_reason, created_at, "
                f"updated_at FROM tasks {where} ORDER BY updated_at DESC, id"
            ).fetchall()
            return tuple(row_task(row) for row in rows)

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def load_task_state(self, thread_id: str) -> TaskState:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> TaskState:
            _require_thread(connection, thread_id)
            row = connection.execute(
                "SELECT payload FROM task_states WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            return TaskState.empty() if row is None else decode_task_state(row["payload"])

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def save_task_state(self, thread_id: str, state: TaskState) -> None:
        thread_id = _text(thread_id, "thread_id")
        payload = encode_task_state(state)
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            _require_thread(connection, thread_id)
            _upsert_task_state(connection, thread_id, payload, timestamp)
            _touch_thread(connection, thread_id, timestamp)

        await self._database.write(write)  # type: ignore[attr-defined]

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
            _upsert_task_state(
                connection, thread_id, encode_task_state(updated), timestamp
            )
            _touch_thread(connection, thread_id, timestamp)
            return updated

        return await self._database.write(write)  # type: ignore[attr-defined]


def _task_row(
    connection: sqlite3.Connection, column: str, value: str
) -> sqlite3.Row | None:
    return connection.execute(
        f"SELECT contract, id, thread_id, status, stop_reason, created_at, "
        f"updated_at FROM tasks WHERE {column} = ?",
        (value,),
    ).fetchone()


def row_task(row: sqlite3.Row) -> TaskRecord:
    try:
        return TaskRecord.from_dict(
            {
                "id": row["id"],
                "thread_id": row["thread_id"],
                "contract": json.loads(row["contract"])["contract"],
                "status": row["status"],
                "stop_reason": row["stop_reason"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SessionCorruptionError("invalid persisted task") from error


def _upsert_task_state(
    connection: sqlite3.Connection,
    thread_id: str,
    payload: str,
    timestamp: str,
) -> None:
    connection.execute(
        "INSERT INTO task_states(thread_id, payload, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(thread_id) DO UPDATE SET payload = excluded.payload, "
        "updated_at = excluded.updated_at",
        (thread_id, payload, timestamp),
    )
