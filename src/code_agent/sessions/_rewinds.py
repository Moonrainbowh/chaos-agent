from __future__ import annotations

import sqlite3

from ._codec import encode_datetime, utc_now
from ._workspace_codec import operation_from_row, require_uuid
from .errors import SessionCorruptionError, SessionNotFound
from .workspace_models import (
    RewindMode,
    RewindOperationRecord,
    RewindOperationStatus,
    WorkspaceLineageStatus,
)


class RewindRepositoryMixin:
    _database: object

    async def begin_rewind(
        self,
        operation: RewindOperationRecord | object,
        rollback_checkpoint_id: str | None = None,
    ) -> RewindOperationRecord:
        record = _coerce_operation(operation, rollback_checkpoint_id)
        if record.status is not RewindOperationStatus.PENDING:
            raise ValueError("new rewind operation must be pending")

        def write(connection: sqlite3.Connection) -> RewindOperationRecord:
            existing = _find_operation(connection, record.id)
            if existing is not None:
                restored = operation_from_row(existing)
                if restored != record:
                    raise ValueError("operation id already identifies different facts")
                return restored
            _require_active_lineage(connection, record.lineage_id)
            _require_checkpoint_lineage(
                connection, record.source_checkpoint_id, record.lineage_id
            )
            if record.rollback_checkpoint_id is not None:
                _require_checkpoint_lineage(
                    connection, record.rollback_checkpoint_id, record.lineage_id
                )
            pending = connection.execute(
                "SELECT 1 FROM rewind_operations WHERE lineage_id = ? AND status = 'pending'",
                (record.lineage_id,),
            ).fetchone()
            if pending is not None:
                raise ValueError("lineage already has a pending rewind")
            connection.execute(
                "INSERT INTO rewind_operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.lineage_id,
                    record.source_checkpoint_id,
                    record.rollback_checkpoint_id,
                    record.mode.value,
                    record.preview_fingerprint,
                    record.status.value,
                    None,
                    None,
                    encode_datetime(record.created_at),
                    encode_datetime(record.updated_at),
                ),
            )
            return record

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def complete_rewind(
        self, operation_id: str, replacement_task_id: str | None = None
    ) -> RewindOperationRecord:
        operation_id = require_uuid(operation_id, "operation_id")
        replacement = (
            None
            if replacement_task_id is None
            else require_uuid(replacement_task_id, "replacement_task_id")
        )
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> RewindOperationRecord:
            current = _require_operation(connection, operation_id)
            _require_generic_completion(current, replacement)
            if current.status is RewindOperationStatus.COMPLETED:
                return current
            if current.status is not RewindOperationStatus.PENDING:
                raise ValueError("only pending rewinds can complete")
            _require_active_lineage(connection, current.lineage_id)
            changed = connection.execute(
                "UPDATE rewind_operations SET status = 'completed', "
                "replacement_task_id = ?, updated_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (replacement, timestamp, operation_id),
            )
            if changed.rowcount != 1:
                raise ValueError("rewind completion lost its compare-and-swap")
            return operation_from_row(_require_operation_row(connection, operation_id))

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def fail_rewind(
        self,
        operation_id: str,
        error_code: str,
        *,
        recovery_required: bool = False,
    ) -> RewindOperationRecord:
        operation_id = require_uuid(operation_id, "operation_id")
        if not isinstance(error_code, str) or not error_code.strip() or len(error_code) > 1_024:
            raise ValueError("error_code must be bounded non-blank text")
        if not isinstance(recovery_required, bool):
            raise TypeError("recovery_required must be a bool")
        target = (
            RewindOperationStatus.RECOVERY_REQUIRED
            if recovery_required
            else RewindOperationStatus.ROLLED_BACK
        )
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> RewindOperationRecord:
            current = _require_operation(connection, operation_id)
            if current.status is target:
                if current.error_code != error_code:
                    raise ValueError("failed rewind error code does not match")
                return current
            if current.status is not RewindOperationStatus.PENDING:
                raise ValueError("only pending rewinds can fail")
            changed = connection.execute(
                "UPDATE rewind_operations SET status = ?, error_code = ?, updated_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (target.value, error_code, timestamp, operation_id),
            )
            if changed.rowcount != 1:
                raise ValueError("rewind failure lost its compare-and-swap")
            if recovery_required:
                connection.execute(
                    "UPDATE workspace_lineages SET status = ?, updated_at = ? WHERE id = ?",
                    (WorkspaceLineageStatus.RECOVERY_REQUIRED.value, timestamp, current.lineage_id),
                )
            return operation_from_row(_require_operation_row(connection, operation_id))

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def pending_rewinds(
        self, lineage_id: str | None = None
    ) -> tuple[RewindOperationRecord, ...]:
        if lineage_id is not None:
            lineage_id = require_uuid(lineage_id, "lineage_id")

        def read(connection: sqlite3.Connection) -> tuple[RewindOperationRecord, ...]:
            if lineage_id is None:
                rows = connection.execute(
                    "SELECT * FROM rewind_operations "
                    "WHERE status IN ('pending', 'recovery_required') "
                    "ORDER BY created_at, id"
                ).fetchall()
            else:
                if connection.execute(
                    "SELECT 1 FROM workspace_lineages WHERE id = ?", (lineage_id,)
                ).fetchone() is None:
                    raise SessionNotFound("workspace lineage not found")
                rows = connection.execute(
                    "SELECT * FROM rewind_operations WHERE lineage_id = ? "
                    "AND status IN ('pending', 'recovery_required') ORDER BY created_at, id",
                    (lineage_id,),
                ).fetchall()
            return tuple(operation_from_row(row) for row in rows)

        return await self._database.read(read)  # type: ignore[attr-defined]


def _coerce_operation(
    value: RewindOperationRecord | object, rollback_checkpoint_id: str | None
) -> RewindOperationRecord:
    if isinstance(value, RewindOperationRecord):
        if rollback_checkpoint_id is not None:
            raise TypeError("rollback checkpoint is already part of the operation")
        return value
    try:
        return RewindOperationRecord.create(
            getattr(value, "lineage_id"),
            getattr(value, "checkpoint_id"),
            rollback_checkpoint_id,
            getattr(value, "mode"),
            getattr(value, "fingerprint"),
            operation_id=getattr(value, "operation_id"),
        )
    except AttributeError as error:
        raise TypeError("operation must be a rewind record or preview") from error


def _require_active_lineage(connection: sqlite3.Connection, lineage_id: str) -> None:
    row = connection.execute(
        "SELECT status FROM workspace_lineages WHERE id = ?", (lineage_id,)
    ).fetchone()
    if row is None:
        raise SessionNotFound("workspace lineage not found")
    try:
        status = WorkspaceLineageStatus(row["status"])
    except (TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted lineage status") from error
    if status is not WorkspaceLineageStatus.ACTIVE:
        raise ValueError("workspace lineage is not writable")


def _checkpoint_lineage(connection: sqlite3.Connection, checkpoint_id: str) -> str:
    row = connection.execute(
        "SELECT s.lineage_id AS snapshot_lineage, ws.lineage_id AS cursor_lineage, "
        "t.workspace_lineage_id AS task_lineage "
        "FROM checkpoints c LEFT JOIN checkpoint_workspace_state ws "
        "ON ws.checkpoint_id = c.id LEFT JOIN workspace_snapshots s ON s.id = ws.snapshot_id "
        "LEFT JOIN tasks t ON t.thread_id = c.thread_id WHERE c.id = ?",
        (checkpoint_id,),
    ).fetchone()
    if row is None:
        raise SessionNotFound("checkpoint not found")
    if row["cursor_lineage"] is None:
        raise SessionCorruptionError("legacy checkpoint is not rewindable")
    values = {
        item
        for item in (row["snapshot_lineage"], row["cursor_lineage"], row["task_lineage"])
        if item
    }
    if len(values) != 1:
        raise SessionCorruptionError("checkpoint lineage is missing or inconsistent")
    return values.pop()


def _require_checkpoint_lineage(
    connection: sqlite3.Connection, checkpoint_id: str, lineage_id: str
) -> None:
    if _checkpoint_lineage(connection, checkpoint_id) != lineage_id:
        raise ValueError("checkpoint belongs to another workspace lineage")


def _require_generic_completion(
    operation: RewindOperationRecord,
    replacement_task_id: str | None,
) -> None:
    if operation.mode is not RewindMode.CODE:
        raise ValueError("session rewinds require atomic session completion")
    if replacement_task_id is not None:
        raise ValueError("code rewind completion cannot publish a replacement task")


def _find_operation(
    connection: sqlite3.Connection, operation_id: str
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM rewind_operations WHERE id = ?", (operation_id,)
    ).fetchone()


def _require_operation_row(
    connection: sqlite3.Connection, operation_id: str
) -> sqlite3.Row:
    row = _find_operation(connection, operation_id)
    if row is None:
        raise SessionNotFound("rewind operation not found")
    return row


def _require_operation(
    connection: sqlite3.Connection, operation_id: str
) -> RewindOperationRecord:
    return operation_from_row(_require_operation_row(connection, operation_id))
