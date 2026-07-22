from __future__ import annotations

import sqlite3
import uuid

from code_agent.core.events import AgentEvent
from code_agent.core.models import Message

from ._codec import (
    decode_datetime,
    decode_event,
    decode_message,
    encode_datetime,
    encode_event,
    encode_message,
    utc_now,
)
from ._records import _require_thread, _text, _touch_thread
from .errors import SessionCorruptionError, SessionNotFound
from .models import MessageRecord, ThreadRelation, ThreadStatus, ThreadSummary


class ThreadContentRepositoryMixin:
    _database: object

    async def create_thread(
        self,
        title: str | None = None,
        *,
        parent_thread_id: str | None = None,
    ) -> str:
        if title is not None:
            title = _text(title, "title")
        if parent_thread_id is not None:
            parent_thread_id = _text(parent_thread_id, "parent_thread_id")
        identifier = uuid.uuid4().hex
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            if parent_thread_id is not None:
                parent = connection.execute(
                    "SELECT parent_thread_id FROM threads WHERE id = ?",
                    (parent_thread_id,),
                ).fetchone()
                if parent is None:
                    raise SessionNotFound("parent thread not found")
                if parent["parent_thread_id"] is not None:
                    raise ValueError("thread trees support only two levels")
            connection.execute(
                "INSERT INTO threads(id, created_at, updated_at, title, status, "
                "parent_thread_id) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    timestamp,
                    timestamp,
                    title,
                    ThreadStatus.ACTIVE.value,
                    parent_thread_id,
                ),
            )

        await self._database.write(write)  # type: ignore[attr-defined]
        return identifier

    async def load_thread_relation(self, thread_id: str) -> ThreadRelation:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> ThreadRelation:
            row = connection.execute(
                "SELECT parent_thread_id FROM threads WHERE id = ?", (thread_id,)
            ).fetchone()
            if row is None:
                raise SessionNotFound("thread not found")
            children = connection.execute(
                "SELECT id FROM threads WHERE parent_thread_id = ? ORDER BY created_at, id",
                (thread_id,),
            ).fetchall()
            return ThreadRelation(
                thread_id,
                row["parent_thread_id"],
                tuple(child["id"] for child in children),
            )

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def load_messages(self, thread_id: str) -> tuple[Message, ...]:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> tuple[Message, ...]:
            _require_thread(connection, thread_id)
            rows = connection.execute(
                "SELECT payload FROM messages WHERE thread_id = ? ORDER BY sequence",
                (thread_id,),
            ).fetchall()
            return tuple(decode_message(row[0]) for row in rows)

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def load_message_records(self, thread_id: str) -> tuple[MessageRecord, ...]:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> tuple[MessageRecord, ...]:
            _require_thread(connection, thread_id)
            rows = connection.execute(
                "SELECT sequence, payload, created_at FROM messages "
                "WHERE thread_id = ? ORDER BY sequence",
                (thread_id,),
            ).fetchall()
            return tuple(
                MessageRecord(
                    row["sequence"],
                    thread_id,
                    decode_message(row["payload"]),
                    decode_datetime(row["created_at"], "message"),
                )
                for row in rows
            )

        return await self._database.read(read)  # type: ignore[attr-defined]

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

        await self._database.write(write)  # type: ignore[attr-defined]

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

        await self._database.write(write)  # type: ignore[attr-defined]

    async def load_events(self, thread_id: str) -> tuple[AgentEvent, ...]:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> tuple[AgentEvent, ...]:
            _require_thread(connection, thread_id)
            rows = connection.execute(
                "SELECT payload FROM events WHERE thread_id = ? ORDER BY sequence",
                (thread_id,),
            ).fetchall()
            return tuple(decode_event(row[0]) for row in rows)

        return await self._database.read(read)  # type: ignore[attr-defined]

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

        await self._database.write(write)  # type: ignore[attr-defined]

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

        return await self._database.read(read)  # type: ignore[attr-defined]


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
