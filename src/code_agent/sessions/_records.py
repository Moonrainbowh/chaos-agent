from __future__ import annotations

import sqlite3
import uuid
from typing import Mapping, Optional

from code_agent.core._json import JSONValue, validate_json_mapping

from ._codec import (
    decode_datetime,
    decode_metadata,
    encode_datetime,
    encode_metadata,
    utc_now,
)
from .errors import SessionCorruptionError, SessionNotFound
from .models import CheckpointRecord, GoalRecord, GoalStatus


class RecordRepositoryMixin:
    _database: object

    async def create_goal(
        self,
        thread_id: str,
        objective: str,
        *,
        metadata: Optional[Mapping[str, JSONValue]] = None,
    ) -> str:
        thread_id = _text(thread_id, "thread_id")
        objective = _text(objective, "objective")
        data: Mapping[str, JSONValue] = {} if metadata is None else metadata
        validate_json_mapping(data, "metadata")
        identifier = uuid.uuid4().hex
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            _require_thread(connection, thread_id)
            connection.execute(
                "INSERT INTO goals VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier, thread_id, objective, GoalStatus.ACTIVE.value,
                    encode_metadata(data), timestamp, timestamp,
                ),
            )
            _touch_thread(connection, thread_id, timestamp)

        await self._database.write(write)  # type: ignore[attr-defined]
        return identifier

    async def update_goal(self, goal_id: str, status: GoalStatus) -> None:
        goal_id = _text(goal_id, "goal_id")
        if not isinstance(status, GoalStatus):
            raise TypeError("status must be a GoalStatus")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                "SELECT thread_id FROM goals WHERE id = ?", (goal_id,)
            ).fetchone()
            if row is None:
                raise SessionNotFound(f"goal not found: {goal_id}")
            connection.execute(
                "UPDATE goals SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, timestamp, goal_id),
            )
            _touch_thread(connection, row[0], timestamp)

        await self._database.write(write)  # type: ignore[attr-defined]

    async def list_goals(self, thread_id: str) -> tuple[GoalRecord, ...]:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> tuple[GoalRecord, ...]:
            _require_thread(connection, thread_id)
            rows = connection.execute(
                "SELECT * FROM goals WHERE thread_id = ? ORDER BY created_at, id",
                (thread_id,),
            ).fetchall()
            try:
                return tuple(
                    GoalRecord(
                        id=row["id"],
                        thread_id=row["thread_id"],
                        objective=row["objective"],
                        status=GoalStatus(row["status"]),
                        metadata=decode_metadata(row["metadata"]),
                        created_at=decode_datetime(row["created_at"], "goal"),
                        updated_at=decode_datetime(row["updated_at"], "goal"),
                    )
                    for row in rows
                )
            except (TypeError, ValueError) as error:
                raise SessionCorruptionError("invalid persisted goal") from error

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def create_checkpoint(
        self,
        thread_id: str,
        label: str,
        metadata: Optional[Mapping[str, JSONValue]] = None,
    ) -> str:
        thread_id = _text(thread_id, "thread_id")
        label = _text(label, "label")
        if label.startswith("context:"):
            raise ValueError("context: is reserved for the internal context journal")
        data: Mapping[str, JSONValue] = {} if metadata is None else metadata
        validate_json_mapping(data, "metadata")
        identifier = uuid.uuid4().hex
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            _require_thread(connection, thread_id)
            message_sequence = _thread_maximum(
                connection, "messages", thread_id
            )
            event_sequence = _thread_maximum(connection, "events", thread_id)
            connection.execute(
                "INSERT INTO checkpoints(id, thread_id, label, metadata, "
                "created_at, message_sequence, event_sequence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    thread_id,
                    label,
                    encode_metadata(data),
                    timestamp,
                    message_sequence,
                    event_sequence,
                ),
            )
            _touch_thread(connection, thread_id, timestamp)

        await self._database.write(write)  # type: ignore[attr-defined]
        return identifier

    async def list_checkpoints(
        self, thread_id: str
    ) -> tuple[CheckpointRecord, ...]:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> tuple[CheckpointRecord, ...]:
            _require_thread(connection, thread_id)
            rows = connection.execute(
                "SELECT * FROM checkpoints WHERE thread_id = ? AND label NOT LIKE 'context:%' ORDER BY created_at, id",
                (thread_id,),
            ).fetchall()
            try:
                return tuple(
                    CheckpointRecord(
                        id=row["id"],
                        thread_id=row["thread_id"],
                        label=row["label"],
                        metadata=decode_metadata(row["metadata"]),
                        created_at=decode_datetime(row["created_at"], "checkpoint"),
                        message_sequence=row["message_sequence"],
                        event_sequence=row["event_sequence"],
                    )
                    for row in rows
                )
            except (TypeError, ValueError) as error:
                raise SessionCorruptionError("invalid persisted checkpoint") from error

        return await self._database.read(read)  # type: ignore[attr-defined]


def _thread_maximum(
    connection: sqlite3.Connection, table: str, thread_id: str
) -> int:
    if table not in {"messages", "events"}:
        raise ValueError("unsupported sequence table")
    row = connection.execute(
        f"SELECT COALESCE(MAX(sequence), 0) FROM {table} WHERE thread_id = ?",
        (thread_id,),
    ).fetchone()
    return int(row[0])


def _require_thread(connection: sqlite3.Connection, thread_id: str) -> None:
    if connection.execute(
        "SELECT 1 FROM threads WHERE id = ?", (thread_id,)
    ).fetchone() is None:
        raise SessionNotFound(f"thread not found: {thread_id}")


def _touch_thread(
    connection: sqlite3.Connection, thread_id: str, timestamp: str
) -> None:
    connection.execute(
        "UPDATE threads SET updated_at = ? WHERE id = ?", (timestamp, thread_id)
    )


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value
