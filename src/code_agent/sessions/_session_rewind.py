from __future__ import annotations

import sqlite3
import uuid

from code_agent.core.task import TaskRecord, TaskStatus

from ._checkpoint_budget import copy_budget
from ._checkpoint_fork import (
    _copy_goals,
    _copy_streams,
    _copy_task_state,
    _fork_source,
    _insert_thread,
    _require_active_lineage,
    _source_task,
)
from ._codec import encode_datetime, encode_task, utc_now
from ._rewinds import _require_operation, _require_operation_row
from ._workspace_codec import cursor_from_row, operation_from_row, require_uuid
from .errors import SessionCorruptionError
from .workspace_models import RewindMode, RewindOperationRecord, RewindOperationStatus


_SESSION_MODES = {RewindMode.SESSION, RewindMode.CODE_AND_SESSION}
_SOURCE_STATUSES = {
    TaskStatus.PAUSED,
    TaskStatus.INTERRUPTED,
    TaskStatus.WAITING_DECISION,
}


class AtomicSessionRewindRepositoryMixin:
    _database: object

    async def complete_session_rewind(
        self,
        operation_id: str,
        source_task_id: str,
        replacement_task_id: str,
    ) -> RewindOperationRecord:
        operation_id = require_uuid(operation_id, "operation_id")
        source_task_id = require_uuid(source_task_id, "source_task_id")
        replacement_task_id = require_uuid(
            replacement_task_id, "replacement_task_id"
        )
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> RewindOperationRecord:
            operation = _require_operation(connection, operation_id)
            if operation.status is RewindOperationStatus.COMPLETED:
                return _require_same_completion(
                    connection, operation, source_task_id, replacement_task_id
                )
            _require_atomic_pending(operation, source_task_id)
            source_row, _, cursor_row = _fork_source(
                connection, operation.source_checkpoint_id
            )
            source = _require_source(source_row, source_task_id, operation.lineage_id)
            cursor = cursor_from_row(cursor_row)
            _require_cursor_lineage(cursor.lineage_id, operation.lineage_id)
            _require_active_lineage(connection, operation.lineage_id)
            _require_owner(connection, operation.lineage_id, source_task_id)
            replacement = _insert_replacement(
                connection, source_row, source, cursor, replacement_task_id,
                operation.lineage_id, timestamp,
            )
            _transfer_owner(
                connection, operation.lineage_id, source_task_id,
                replacement.id, timestamp,
            )
            _supersede_source(connection, source, replacement.id)
            _complete_operation(connection, operation.id, replacement.id, timestamp)
            return operation_from_row(_require_operation_row(connection, operation.id))

        return await self._database.write(write)  # type: ignore[attr-defined]


def _require_atomic_pending(
    operation: RewindOperationRecord, source_task_id: str
) -> None:
    if operation.status is not RewindOperationStatus.PENDING:
        raise ValueError("only pending session rewinds can complete atomically")
    if operation.mode not in _SESSION_MODES:
        raise ValueError("atomic session completion requires a session rewind mode")
    if operation.rollback_checkpoint_id is None:
        raise ValueError("session rewind is missing its rollback checkpoint")
    if not source_task_id:
        raise ValueError("source task is required")


def _require_source(
    source_row: sqlite3.Row,
    source_task_id: str,
    lineage_id: str,
) -> TaskRecord:
    source = _source_task(source_row)
    if source.id != source_task_id:
        raise ValueError("rewind source task does not own the checkpoint")
    if source_row["workspace_lineage_id"] != lineage_id:
        raise SessionCorruptionError("rewind source task lineage is inconsistent")
    if source.status not in _SOURCE_STATUSES:
        raise ValueError("rewind source task is not quiescent")
    return source


def _require_cursor_lineage(cursor_lineage: str | None, lineage_id: str) -> None:
    if cursor_lineage != lineage_id:
        raise SessionCorruptionError("rewind checkpoint cursor lineage is inconsistent")


def _require_owner(
    connection: sqlite3.Connection, lineage_id: str, source_task_id: str
) -> None:
    row = connection.execute(
        "SELECT owner_task_id FROM workspace_lineages WHERE id = ?", (lineage_id,)
    ).fetchone()
    if row is None or row["owner_task_id"] != source_task_id:
        raise ValueError("rewind source task no longer owns the lineage")


def _insert_replacement(
    connection: sqlite3.Connection,
    source_row: sqlite3.Row,
    source: TaskRecord,
    cursor: object,
    task_id: str,
    lineage_id: str,
    timestamp: str,
) -> TaskRecord:
    thread_id = uuid.uuid4().hex
    _insert_thread(connection, source_row, thread_id, timestamp)
    _copy_streams(connection, source.thread_id, thread_id, cursor)
    _copy_goals(connection, thread_id, cursor.goals_payload)
    _copy_task_state(connection, thread_id, cursor.task_state_payload, timestamp)
    copy_budget(
        connection, source.thread_id, thread_id, lineage_id, cursor.budget_payload
    )
    replacement = TaskRecord(task_id, thread_id, source.contract).transition(
        TaskStatus.PAUSED, "created by rewind"
    )
    connection.execute(
        "INSERT INTO tasks(id, thread_id, contract, status, stop_reason, created_at, "
        "updated_at, workspace_lineage_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            replacement.id, replacement.thread_id, encode_task(replacement),
            replacement.status.value, replacement.stop_reason,
            encode_datetime(replacement.created_at),
            encode_datetime(replacement.updated_at), lineage_id,
        ),
    )
    return replacement


def _transfer_owner(
    connection: sqlite3.Connection,
    lineage_id: str,
    source_task_id: str,
    replacement_task_id: str,
    timestamp: str,
) -> None:
    changed = connection.execute(
        "UPDATE workspace_lineages SET owner_task_id = ?, updated_at = ? "
        "WHERE id = ? AND owner_task_id = ? AND status = 'active'",
        (replacement_task_id, timestamp, lineage_id, source_task_id),
    )
    if changed.rowcount != 1:
        raise ValueError("session rewind owner transfer lost its compare-and-swap")


def _supersede_source(
    connection: sqlite3.Connection, source: TaskRecord, replacement_task_id: str
) -> None:
    updated = source.transition(
        TaskStatus.SUPERSEDED, f"replaced by rewind {replacement_task_id}"
    )
    changed = connection.execute(
        "UPDATE tasks SET contract = ?, status = ?, stop_reason = ?, updated_at = ? "
        "WHERE id = ? AND status = ?",
        (
            encode_task(updated), updated.status.value, updated.stop_reason,
            encode_datetime(updated.updated_at), updated.id, source.status.value,
        ),
    )
    if changed.rowcount != 1:
        raise ValueError("session rewind source transition lost its compare-and-swap")


def _complete_operation(
    connection: sqlite3.Connection,
    operation_id: str,
    replacement_task_id: str,
    timestamp: str,
) -> None:
    changed = connection.execute(
        "UPDATE rewind_operations SET status = 'completed', replacement_task_id = ?, "
        "updated_at = ? WHERE id = ? AND status = 'pending'",
        (replacement_task_id, timestamp, operation_id),
    )
    if changed.rowcount != 1:
        raise ValueError("session rewind completion lost its compare-and-swap")


def _require_same_completion(
    connection: sqlite3.Connection,
    operation: RewindOperationRecord,
    source_task_id: str,
    replacement_task_id: str,
) -> RewindOperationRecord:
    if operation.replacement_task_id != replacement_task_id:
        raise ValueError("completed session rewind replacement does not match")
    source_row, _, _ = _fork_source(connection, operation.source_checkpoint_id)
    if source_row["id"] != source_task_id:
        raise ValueError("completed session rewind source does not match")
    return operation
