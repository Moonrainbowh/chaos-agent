from __future__ import annotations

import sqlite3

from .edit_batch_models import (
    EditBatchOperation,
    EditBatchPath,
    EditBatchPrepare,
    EditBatchRecord,
)
from .errors import SessionStorageError


_UNRESOLVED_SQL = "('prepared','applying','rolling_back','conflicted')"


def require_workspace_available(
    connection: sqlite3.Connection, workspace_fingerprint: str
) -> None:
    batch = connection.execute(
        "SELECT 1 FROM workspace_edit_batches WHERE workspace_fingerprint = ? "
        f"AND state IN {_UNRESOLVED_SQL} LIMIT 1",
        (workspace_fingerprint,),
    ).fetchone()
    if batch is not None:
        raise SessionStorageError("workspace has an unresolved edit batch")
    legacy = connection.execute(
        "SELECT 1 FROM workspace_mutations WHERE workspace_fingerprint = ? "
        "AND status = 'prepared' LIMIT 1",
        (workspace_fingerprint,),
    ).fetchone()
    if legacy is not None:
        raise SessionStorageError("workspace has an unresolved mutation")


def insert_edit_batch(
    connection: sqlite3.Connection,
    sequence: int,
    request: EditBatchPrepare,
    timestamp: str,
) -> None:
    connection.execute(
        "INSERT INTO workspace_edit_batches("
        "mutation_sequence, workspace_fingerprint, plan_id, plan_digest, state, "
        "conflict_code, operation_count, created_at, updated_at, settled_at"
        ") VALUES (?, ?, ?, ?, 'prepared', NULL, ?, ?, ?, NULL)",
        (
            sequence,
            request.mutation.coverage.workspace_fingerprint,
            request.plan_id,
            request.plan_digest,
            len(request.operations),
            timestamp,
            timestamp,
        ),
    )
    for ordinal, operation in enumerate(request.operations):
        insert_edit_batch_operation(connection, sequence, ordinal, operation)


def insert_edit_batch_operation(
    connection: sqlite3.Connection,
    sequence: int,
    ordinal: int,
    operation: EditBatchOperation,
) -> None:
    source = _endpoint(operation.source)
    target = _endpoint(operation.target)
    connection.execute(
        "INSERT INTO workspace_edit_batch_operations("
        "mutation_sequence, ordinal, kind, source_path, source_pre_existed, "
        "source_pre_sha256, source_pre_size, source_post_existed, "
        "source_post_sha256, source_post_size, target_path, "
        "target_pre_existed, target_pre_sha256, target_pre_size, "
        "target_post_existed, target_post_sha256, target_post_size, "
        "case_only, progress, committed_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "'pending', NULL)",
        (
            sequence,
            ordinal,
            operation.kind.value,
            *source,
            *target,
            int(operation.case_only),
        ),
    )


def batch_matches(record: EditBatchRecord, request: EditBatchPrepare) -> bool:
    return (
        record.plan_id == request.plan_id
        and record.plan_digest == request.plan_digest
        and tuple(item.operation for item in record.operations) == request.operations
    )


def _endpoint(endpoint: EditBatchPath | None) -> tuple[object, ...]:
    if endpoint is None:
        return (None, None, None, None, None, None, None)
    return (
        endpoint.path,
        int(endpoint.before_existed),
        endpoint.before_sha256,
        endpoint.before_size,
        int(endpoint.after_existed),
        endpoint.after_sha256,
        endpoint.after_size,
    )
