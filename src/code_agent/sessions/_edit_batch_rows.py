from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from ._codec import decode_datetime
from ._rewind_mutation_sql import _load_by_id
from .edit_batch_models import (
    EditBatchOperation,
    EditBatchOperationKind,
    EditBatchOperationProgress,
    EditBatchOperationRecord,
    EditBatchPath,
    EditBatchRecord,
    EditBatchState,
)
from .errors import SessionCorruptionError, SessionNotFound


def load_edit_batch(
    connection: sqlite3.Connection, mutation_id: str
) -> EditBatchRecord:
    row = connection.execute(
        "SELECT b.*, m.mutation_id FROM workspace_edit_batches b "
        "JOIN workspace_mutations m ON m.sequence = b.mutation_sequence "
        "WHERE m.mutation_id = ?",
        (mutation_id,),
    ).fetchone()
    if row is None:
        raise SessionNotFound("edit batch not found")
    return edit_batch_record(connection, row)


def edit_batch_record(
    connection: sqlite3.Connection, row: sqlite3.Row
) -> EditBatchRecord:
    try:
        sequence = row["mutation_sequence"]
        operation_rows = connection.execute(
            "SELECT * FROM workspace_edit_batch_operations "
            "WHERE mutation_sequence = ? ORDER BY ordinal",
            (sequence,),
        ).fetchall()
        count = row["operation_count"]
        if type(count) is not int or count != len(operation_rows):
            raise ValueError("invalid edit batch operation count")
        mutation = _load_by_id(connection, row["mutation_id"])
        if mutation.sequence != sequence:
            raise ValueError("edit batch mutation sequence moved")
        if mutation.coverage.workspace_fingerprint != row["workspace_fingerprint"]:
            raise ValueError("edit batch workspace fingerprint moved")
        return EditBatchRecord(
            mutation,
            row["plan_id"],
            row["plan_digest"],
            EditBatchState(row["state"]),
            row["conflict_code"],
            _operation_records(operation_rows),
            decode_datetime(row["created_at"], "edit batch"),
            decode_datetime(row["updated_at"], "edit batch"),
            (
                None
                if row["settled_at"] is None
                else decode_datetime(row["settled_at"], "edit batch")
            ),
        )
    except SessionCorruptionError:
        raise
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid edit batch record") from error


def _operation_records(
    rows: Sequence[sqlite3.Row],
) -> tuple[EditBatchOperationRecord, ...]:
    records: list[EditBatchOperationRecord] = []
    for expected, row in enumerate(rows):
        if row["ordinal"] != expected:
            raise ValueError("invalid edit batch operation ordinal")
        source = _source_path(row)
        target = _target_path(row)
        operation = EditBatchOperation(
            EditBatchOperationKind(row["kind"]),
            source,
            target,
            case_only=_flag(row["case_only"], "case_only"),
        )
        committed_at = (
            None
            if row["committed_at"] is None
            else decode_datetime(row["committed_at"], "edit batch operation")
        )
        records.append(
            EditBatchOperationRecord(
                expected,
                operation,
                EditBatchOperationProgress(row["progress"]),
                committed_at,
            )
        )
    return tuple(records)


def _source_path(row: sqlite3.Row) -> EditBatchPath | None:
    path = row["source_path"]
    values = (
        row["source_pre_existed"],
        row["source_pre_sha256"],
        row["source_pre_size"],
        row["source_post_existed"],
        row["source_post_sha256"],
        row["source_post_size"],
    )
    if path is None:
        if any(item is not None for item in values):
            raise ValueError("missing source path has endpoint facts")
        return None
    return EditBatchPath(
        path,
        _flag(values[0], "source_pre_existed"),
        values[1],
        values[2],
        _flag(values[3], "source_post_existed"),
        values[4],
        values[5],
    )


def _target_path(row: sqlite3.Row) -> EditBatchPath:
    return EditBatchPath(
        row["target_path"],
        _flag(row["target_pre_existed"], "target_pre_existed"),
        row["target_pre_sha256"],
        row["target_pre_size"],
        _flag(row["target_post_existed"], "target_post_existed"),
        row["target_post_sha256"],
        row["target_post_size"],
    )


def _flag(value: object, label: str) -> bool:
    if value not in (0, 1):
        raise ValueError(f"invalid {label}")
    return bool(value)
