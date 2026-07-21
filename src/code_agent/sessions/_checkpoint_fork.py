from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import Mapping, cast

from code_agent.core._json import JSONValue, plain
from code_agent.core.task import TaskRecord
from code_agent.core.task_state import TaskState

from ._checkpoint_budget import copy_budget
from ._codec import (
    decode_datetime,
    decode_task,
    encode_datetime,
    encode_metadata,
    encode_task,
    encode_task_state,
    utc_now,
)
from ._records import _text
from ._workspace_codec import cursor_from_row, lineage_from_row, require_uuid
from .errors import SessionCorruptionError, SessionNotFound
from .models import GoalStatus, ThreadStatus
from .workspace_models import WorkspaceLineageRecord, WorkspaceLineageStatus


class CheckpointForkRepositoryMixin:
    _database: object

    async def fork_task_from_checkpoint(self, checkpoint_id: str) -> TaskRecord:
        checkpoint_id = require_uuid(checkpoint_id, "checkpoint_id")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> TaskRecord:
            source_row, checkpoint_row, cursor_row = _fork_source(
                connection, checkpoint_id
            )
            source = _source_task(source_row)
            cursor = cursor_from_row(cursor_row)
            lineage_id = source_row["workspace_lineage_id"]
            if lineage_id is None:
                raise SessionCorruptionError("checkpoint task has no workspace lineage")
            _require_active_lineage(connection, lineage_id)
            thread_id = uuid.uuid4().hex
            task_id = uuid.uuid4().hex
            _insert_thread(connection, source_row, thread_id, timestamp)
            _copy_streams(connection, source.thread_id, thread_id, cursor)
            _copy_goals(connection, thread_id, cursor.goals_payload)
            _copy_task_state(connection, thread_id, cursor.task_state_payload, timestamp)
            copy_budget(connection, source.thread_id, thread_id, lineage_id, cursor.budget_payload)
            record = TaskRecord(task_id, thread_id, source.contract)
            connection.execute(
                "INSERT INTO tasks(id, thread_id, contract, status, stop_reason, "
                "created_at, updated_at, workspace_lineage_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.thread_id,
                    encode_task(record),
                    record.status.value,
                    None,
                    encode_datetime(record.created_at),
                    encode_datetime(record.updated_at),
                    lineage_id,
                ),
            )
            return record

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def transfer_lineage_owner(
        self,
        lineage_id: str,
        expected_owner_task_id: str,
        new_owner_task_id: str,
    ) -> WorkspaceLineageRecord:
        lineage_id = require_uuid(lineage_id, "lineage_id")
        expected = require_uuid(expected_owner_task_id, "expected_owner_task_id")
        new_owner = require_uuid(new_owner_task_id, "new_owner_task_id")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> WorkspaceLineageRecord:
            lineage_row = connection.execute(
                "SELECT * FROM workspace_lineages WHERE id = ?", (lineage_id,)
            ).fetchone()
            if lineage_row is None:
                raise SessionNotFound("workspace lineage not found")
            lineage = lineage_from_row(lineage_row)
            if lineage.status is not WorkspaceLineageStatus.ACTIVE:
                raise ValueError("workspace lineage is not transferable")
            if lineage.owner_task_id == new_owner:
                return lineage
            if lineage.owner_task_id != expected:
                raise ValueError("workspace lineage owner changed")
            old_status = _task_status(connection, expected, lineage_id)
            new_status = _task_status(connection, new_owner, lineage_id)
            if old_status not in _RELEASABLE_TASK_STATUSES:
                raise ValueError("current owner task is not quiescent")
            if new_status not in _REPLACEMENT_TASK_STATUSES:
                raise ValueError("replacement task is not in a transferable state")
            changed = connection.execute(
                "UPDATE workspace_lineages SET owner_task_id = ?, updated_at = ? "
                "WHERE id = ? AND owner_task_id = ? AND status = 'active'",
                (new_owner, timestamp, lineage_id, expected),
            )
            if changed.rowcount != 1:
                raise ValueError("owner transfer lost its compare-and-swap")
            return lineage_from_row(
                connection.execute(
                    "SELECT * FROM workspace_lineages WHERE id = ?", (lineage_id,)
                ).fetchone()
            )

        return await self._database.write(write)  # type: ignore[attr-defined]


_RELEASABLE_TASK_STATUSES = {
    "paused", "interrupted", "waiting_decision", "completed", "accepted_partial",
    "failed", "superseded",
}
_REPLACEMENT_TASK_STATUSES = {"created", "paused", "interrupted"}


def _fork_source(
    connection: sqlite3.Connection, checkpoint_id: str
) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row]:
    checkpoint = connection.execute(
        "SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,)
    ).fetchone()
    if checkpoint is None:
        raise SessionNotFound("checkpoint not found")
    cursor = connection.execute(
        "SELECT * FROM checkpoint_workspace_state WHERE checkpoint_id = ?",
        (checkpoint_id,),
    ).fetchone()
    if cursor is None:
        raise SessionNotFound("checkpoint has no rewind cursor")
    task = connection.execute(
        "SELECT * FROM tasks WHERE thread_id = ?", (checkpoint["thread_id"],)
    ).fetchone()
    if task is None:
        raise SessionNotFound("checkpoint thread has no task")
    return task, checkpoint, cursor


def _source_task(row: sqlite3.Row) -> TaskRecord:
    try:
        persisted = decode_task(row["contract"])
        return TaskRecord(
            row["id"],
            row["thread_id"],
            persisted.contract,
            persisted.status.__class__(row["status"]),
            row["stop_reason"],
            decode_datetime(row["created_at"], "task"),
            decode_datetime(row["updated_at"], "task"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted checkpoint task") from error


def _require_active_lineage(connection: sqlite3.Connection, lineage_id: str) -> None:
    row = connection.execute(
        "SELECT status FROM workspace_lineages WHERE id = ?", (lineage_id,)
    ).fetchone()
    if row is None:
        raise SessionCorruptionError("checkpoint lineage is missing")
    if row["status"] != WorkspaceLineageStatus.ACTIVE.value:
        raise ValueError("checkpoint lineage is not writable")


def _insert_thread(
    connection: sqlite3.Connection,
    source_task: sqlite3.Row,
    thread_id: str,
    timestamp: str,
) -> None:
    source = connection.execute(
        "SELECT * FROM threads WHERE id = ?", (source_task["thread_id"],)
    ).fetchone()
    if source is None:
        raise SessionCorruptionError("source task thread is missing")
    parent = source["id"] if source["parent_thread_id"] is None else source["parent_thread_id"]
    title = None if source["title"] is None else f"{source['title']} (rewind)"[:1024]
    connection.execute(
        "INSERT INTO threads(id, created_at, updated_at, title, status, parent_thread_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (thread_id, timestamp, timestamp, title, ThreadStatus.ACTIVE.value, parent),
    )


def _copy_streams(
    connection: sqlite3.Connection,
    source_thread: str,
    target_thread: str,
    cursor: object,
) -> None:
    for table, boundary in (
        ("messages", cursor.message_sequence),
        ("events", cursor.event_sequence),
    ):
        connection.execute(
            f"INSERT INTO {table}(thread_id, payload, created_at) "
            f"SELECT ?, payload, created_at FROM {table} "
            "WHERE thread_id = ? AND sequence <= ? ORDER BY sequence",
            (target_thread, source_thread, boundary),
        )


def _copy_goals(
    connection: sqlite3.Connection,
    thread_id: str,
    payloads: tuple[Mapping[str, JSONValue], ...],
) -> None:
    for payload in payloads:
        try:
            status = GoalStatus(cast(str, payload["status"]))
            objective = _text(payload["objective"], "goal objective")
            metadata = cast(Mapping[str, JSONValue], plain(payload.get("metadata", {})))
            created = _payload_time(payload.get("created_at"))
            updated = _payload_time(payload.get("updated_at"), default=created)
        except (KeyError, TypeError, ValueError) as error:
            raise SessionCorruptionError("invalid checkpoint goal payload") from error
        connection.execute(
            "INSERT INTO goals VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, thread_id, objective, status.value, encode_metadata(metadata), created, updated),
        )


def _copy_task_state(
    connection: sqlite3.Connection,
    thread_id: str,
    payload: Mapping[str, JSONValue],
    timestamp: str,
) -> None:
    try:
        state = TaskState.from_dict(cast(Mapping[str, object], plain(payload)))
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid checkpoint task state") from error
    connection.execute(
        "INSERT INTO task_states VALUES (?, ?, ?)",
        (thread_id, encode_task_state(state), timestamp),
    )


def _task_status(
    connection: sqlite3.Connection, task_id: str, lineage_id: str
) -> str:
    row = connection.execute(
        "SELECT status, workspace_lineage_id FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        raise SessionNotFound("task not found")
    if row["workspace_lineage_id"] != lineage_id:
        raise ValueError("task belongs to another workspace lineage")
    return cast(str, row["status"])


def _payload_time(value: object, *, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    return encode_datetime(decode_datetime(value, "checkpoint goal"))
