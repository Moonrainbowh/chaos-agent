from __future__ import annotations
import sqlite3
import uuid
from typing import Mapping

from code_agent.core._json import JSONValue, validate_json_mapping

from ._codec import encode_datetime, encode_metadata, utc_now
from ._records import _require_thread, _text, _touch_thread
from ._workspace_codec import (
    cursor_from_row,
    insert_cursor,
    insert_lineage,
    insert_snapshot,
    lineage_from_row,
    require_uuid,
    snapshot_from_rows,
)
from ._workspace_ownership import (
    bind_task_lineage,
    require_checkpoint_owner,
    require_owner_task,
)
from .errors import SessionCorruptionError, SessionNotFound
from ._task_budget import sync_lineage_usage, task_budget
from .workspace_models import (
    CheckpointCursor,
    WorkspaceLineageRecord,
    WorkspaceSnapshotRecord,
    WorkspaceSnapshotStatus,
)


class WorkspaceSnapshotRepositoryMixin:
    _database: object

    async def create_lineage(
        self, record: WorkspaceLineageRecord
    ) -> WorkspaceLineageRecord:
        if not isinstance(record, WorkspaceLineageRecord):
            raise TypeError("record must be a WorkspaceLineageRecord")

        def write(connection: sqlite3.Connection) -> WorkspaceLineageRecord:
            existing = connection.execute(
                "SELECT * FROM workspace_lineages WHERE id = ?", (record.id,)
            ).fetchone()
            if existing is not None:
                restored = lineage_from_row(existing)
                if restored != record:
                    raise ValueError("lineage id already identifies different facts")
                return restored
            require_owner_task(connection, record.owner_task_id)
            insert_lineage(connection, record)
            connection.execute(
                "INSERT INTO workspace_lineage_usage(lineage_id) VALUES (?)",
                (record.id,),
            )
            if record.owner_task_id is not None:
                bind_task_lineage(connection, record.owner_task_id, record.id)
                budget = connection.execute(
                    "SELECT b.* FROM task_budgets b JOIN tasks t ON t.thread_id = b.thread_id "
                    "WHERE t.id = ?",
                    (record.owner_task_id,),
                ).fetchone()
                if budget is not None:
                    task = connection.execute(
                        "SELECT thread_id FROM tasks WHERE id = ?", (record.owner_task_id,)
                    ).fetchone()
                    sync_lineage_usage(connection, task["thread_id"], task_budget(budget))
            return record

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def load_lineage(self, lineage_id: str) -> WorkspaceLineageRecord:
        lineage_id = require_uuid(lineage_id, "lineage_id")

        def read(connection: sqlite3.Connection) -> WorkspaceLineageRecord:
            row = connection.execute(
                "SELECT * FROM workspace_lineages WHERE id = ?", (lineage_id,)
            ).fetchone()
            if row is None:
                raise SessionNotFound("workspace lineage not found")
            return lineage_from_row(row)

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def load_lineage_for_task(self, task_id: str) -> WorkspaceLineageRecord:
        task_id = require_uuid(task_id, "task_id")

        def read(connection: sqlite3.Connection) -> WorkspaceLineageRecord:
            row = connection.execute(
                "SELECT l.* FROM tasks t JOIN workspace_lineages l "
                "ON l.id = t.workspace_lineage_id WHERE t.id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                task = connection.execute(
                    "SELECT 1 FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                if task is None:
                    raise SessionNotFound("task not found")
                raise SessionNotFound("task has no workspace lineage")
            return lineage_from_row(row)

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def publish_workspace_checkpoint(
        self,
        thread_id: str,
        label: str,
        metadata: Mapping[str, JSONValue],
        snapshot: WorkspaceSnapshotRecord | None,
        cursor: CheckpointCursor,
    ) -> str:
        thread_id = _text(thread_id, "thread_id")
        label = _text(label, "label")
        validate_json_mapping(metadata, "metadata")
        if snapshot is not None and not isinstance(snapshot, WorkspaceSnapshotRecord):
            raise TypeError("snapshot must be a WorkspaceSnapshotRecord or None")
        if not isinstance(cursor, CheckpointCursor):
            raise TypeError("cursor must be a CheckpointCursor")
        _require_snapshot_status(snapshot, cursor.snapshot_status)
        identifier = uuid.uuid4().hex
        timestamp = encode_datetime(utc_now())
        enriched = dict(metadata)
        enriched["snapshot_id"] = None if snapshot is None else snapshot.id
        enriched["snapshot_status"] = cursor.snapshot_status.value

        def write(connection: sqlite3.Connection) -> str:
            return _publish_checkpoint(
                connection,
                identifier,
                thread_id,
                label,
                enriched,
                snapshot,
                cursor,
                timestamp,
            )

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def load_workspace_snapshot(
        self, checkpoint: str | object
    ) -> WorkspaceSnapshotRecord | None:
        checkpoint_id = require_uuid(
            getattr(checkpoint, "id", checkpoint), "checkpoint_id"
        )

        def read(connection: sqlite3.Connection) -> WorkspaceSnapshotRecord | None:
            state = _checkpoint_state(connection, checkpoint_id)
            try:
                status = WorkspaceSnapshotStatus(state["snapshot_status"])
            except (TypeError, ValueError) as error:
                raise SessionCorruptionError("invalid snapshot availability status") from error
            snapshot_id = state["snapshot_id"]
            if status is WorkspaceSnapshotStatus.UNAVAILABLE:
                if snapshot_id is not None:
                    raise SessionCorruptionError("unavailable checkpoint references a snapshot")
                return None
            if snapshot_id is None:
                raise SessionCorruptionError("available checkpoint is missing a snapshot")
            row = connection.execute(
                "SELECT * FROM workspace_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
            if row is None:
                raise SessionCorruptionError("checkpoint snapshot is missing")
            entries = connection.execute(
                "SELECT * FROM workspace_snapshot_entries WHERE snapshot_id = ? "
                "ORDER BY relative_path",
                (snapshot_id,),
            ).fetchall()
            return snapshot_from_rows(row, entries)

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def load_checkpoint_cursor(self, checkpoint_id: str) -> CheckpointCursor:
        checkpoint_id = require_uuid(checkpoint_id, "checkpoint_id")

        def read(connection: sqlite3.Connection) -> CheckpointCursor:
            return cursor_from_row(_checkpoint_state(connection, checkpoint_id))

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def latest_event_sequence(self, thread_id: str) -> int:
        thread_id = _text(thread_id, "thread_id")

        def read(connection: sqlite3.Connection) -> int:
            _require_thread(connection, thread_id)
            return _latest_sequence(connection, "events", thread_id)

        return await self._database.read(read)  # type: ignore[attr-defined]


def _publish_checkpoint(
    connection: sqlite3.Connection,
    identifier: str,
    thread_id: str,
    label: str,
    metadata: Mapping[str, JSONValue],
    snapshot: WorkspaceSnapshotRecord | None,
    cursor: CheckpointCursor,
    timestamp: str,
) -> str:
    _require_thread(connection, thread_id)
    _require_cursor_boundary(connection, thread_id, cursor)
    require_checkpoint_owner(connection, thread_id, cursor, snapshot)
    if snapshot is not None:
        insert_snapshot(connection, snapshot)
    connection.execute(
        "INSERT INTO checkpoints(id, thread_id, label, metadata, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (identifier, thread_id, label, encode_metadata(metadata), timestamp),
    )
    insert_cursor(
        connection, identifier, None if snapshot is None else snapshot.id, cursor
    )
    _touch_thread(connection, thread_id, timestamp)
    return identifier


def _require_snapshot_status(
    snapshot: WorkspaceSnapshotRecord | None, status: WorkspaceSnapshotStatus
) -> None:
    available = status is WorkspaceSnapshotStatus.AVAILABLE
    if available != (snapshot is not None):
        raise ValueError("snapshot and snapshot_status are inconsistent")


def _require_cursor_boundary(
    connection: sqlite3.Connection, thread_id: str, cursor: CheckpointCursor
) -> None:
    message = _latest_sequence(connection, "messages", thread_id)
    event = _latest_sequence(connection, "events", thread_id)
    if (cursor.message_sequence, cursor.event_sequence) != (message, event):
        raise ValueError("checkpoint cursor does not match the thread boundary")


def _latest_sequence(
    connection: sqlite3.Connection, table: str, thread_id: str
) -> int:
    row = connection.execute(
        f"SELECT COALESCE(MAX(sequence), 0) FROM {table} WHERE thread_id = ?",
        (thread_id,),
    ).fetchone()
    return int(row[0])


def _checkpoint_state(
    connection: sqlite3.Connection, checkpoint_id: str
) -> sqlite3.Row:
    checkpoint = connection.execute(
        "SELECT 1 FROM checkpoints WHERE id = ?", (checkpoint_id,)
    ).fetchone()
    if checkpoint is None:
        raise SessionNotFound("checkpoint not found")
    state = connection.execute(
        "SELECT * FROM checkpoint_workspace_state WHERE checkpoint_id = ?",
        (checkpoint_id,),
    ).fetchone()
    if state is None:
        raise SessionNotFound("checkpoint has no workspace state")
    if state["lineage_id"] is None:
        raise SessionCorruptionError("legacy checkpoint is not rewindable")
    return state
