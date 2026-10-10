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


def append_message_record(connection, thread_id, message, timestamp):
    """Shared message/node/index write inside the caller's transaction."""
    _require_thread(connection, thread_id)
    from .conversation_tree import _ensure_nodes
    from ._conversation_schema import record_message_node
    _ensure_nodes(connection, thread_id)
    cursor = connection.execute("INSERT INTO messages(thread_id,payload,created_at) VALUES (?,?,?)",
                                (thread_id, encode_message(message), timestamp))
    record_message_node(connection, thread_id, cursor.lastrowid)
    from ._history_display import record_history_display
    record_history_display(connection, cursor.lastrowid, message)
    from ._history_queries import stable_item_id
    connection.execute("INSERT INTO history_item_ids(thread_id,sequence,item_id) VALUES (?,?,?)",
                       (thread_id, cursor.lastrowid, stable_item_id(thread_id, cursor.lastrowid)))
    _touch_thread(connection, thread_id, timestamp)


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

    async def create_thread_from_history(
        self, source_thread_id: str, title: str | None = None
    ) -> str:
        """Atomically copy only messages into an active continuation thread.

        Omitted titles inherit the source title. The continuation belongs to the
        original root, preserving the two-level tree. Message payloads and times
        are unchanged; copied records receive new sequence IDs. Execution state,
        authorization, events, goals, checkpoints and budgets are not inherited.
        """
        source_thread_id = _text(source_thread_id, "source_thread_id")
        if title is not None:
            title = _text(title, "title")
        identifier = uuid.uuid4().hex
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            source = connection.execute(
                "SELECT title, parent_thread_id FROM threads WHERE id = ?",
                (source_thread_id,),
            ).fetchone()
            if source is None:
                raise SessionNotFound("source thread not found")
            parent_id = source["parent_thread_id"] or source_thread_id
            if source["parent_thread_id"] is not None:
                parent = connection.execute(
                    "SELECT parent_thread_id FROM threads WHERE id = ?", (parent_id,)
                ).fetchone()
                if parent is None or parent["parent_thread_id"] is not None:
                    raise SessionCorruptionError("source thread has no valid root")
            connection.execute(
                "INSERT INTO threads(id, created_at, updated_at, title, status, "
                "parent_thread_id) VALUES (?, ?, ?, ?, ?, ?)",
                (identifier, timestamp, timestamp, title if title is not None else source["title"],
                 ThreadStatus.ACTIVE.value, parent_id),
            )
            from .conversation_tree import _ensure_nodes
            from ._conversation_schema import copy_message_prefix
            _ensure_nodes(connection, source_thread_id)
            copy_message_prefix(connection, source_thread_id, identifier)

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

    async def load_message_records(
        self,
        thread_id: str,
        *,
        before_sequence: int | None = None,
        limit: int | None = None,
    ) -> tuple[MessageRecord, ...]:
        """Read chronological records, optionally the latest page before a cursor.

        The sequence cursor is exclusive and non-negative. An omitted limit
        keeps the existing all-records behavior; a supplied limit is positive.
        """
        thread_id = _text(thread_id, "thread_id")
        if before_sequence is not None:
            if isinstance(before_sequence, bool) or not isinstance(before_sequence, int):
                raise TypeError("before_sequence must be an integer or None")
            if before_sequence < 0:
                raise ValueError("before_sequence must not be negative")
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise TypeError("limit must be an integer or None")
            if limit <= 0:
                raise ValueError("limit must be positive")

        def read(connection: sqlite3.Connection) -> tuple[MessageRecord, ...]:
            _require_thread(connection, thread_id)
            query = (
                "SELECT sequence, payload, created_at FROM messages "
                "WHERE thread_id = ?"
            )
            parameters: list[object] = [thread_id]
            if before_sequence is not None:
                query += " AND sequence < ?"
                parameters.append(before_sequence)
            query += " ORDER BY sequence ASC" if limit is None else " ORDER BY sequence DESC LIMIT ?"
            if limit is not None:
                parameters.append(limit)
            rows = connection.execute(query, parameters).fetchall()
            if limit is not None:
                rows.reverse()
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
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            append_message_record(connection, thread_id, message, timestamp)

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
        self, *, limit: int = 100, include_archived: bool = False, offset: int = 0
    ) -> tuple[ThreadSummary, ...]:
        """Read one page ordered by updated_at descending, then ID ascending."""
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit <= 0 or limit > 1_000:
            raise ValueError("limit must be between 1 and 1000")
        if not isinstance(include_archived, bool):
            raise TypeError("include_archived must be a bool")
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise TypeError("offset must be an integer")
        if offset < 0:
            raise ValueError("offset must not be negative")

        def read(connection: sqlite3.Connection) -> tuple[ThreadSummary, ...]:
            where = "" if include_archived else "WHERE t.status = 'active'"
            rows = connection.execute(
                f"""SELECT t.*,
                    (SELECT COUNT(*) FROM messages m WHERE m.thread_id = t.id) AS message_count,
                    (SELECT payload FROM messages m WHERE m.thread_id = t.id ORDER BY sequence DESC LIMIT 1) AS last_payload
                    FROM threads t {where}
                    ORDER BY t.updated_at DESC, t.id ASC LIMIT ? OFFSET ?""",
                (limit, offset),
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
