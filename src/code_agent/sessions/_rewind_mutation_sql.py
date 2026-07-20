from __future__ import annotations

import sqlite3

from ._records import _require_thread
from ._rewind_integrity import _require_coverage_integrity
from ._rewind_rows import coverage_record, mutation_record
from .errors import SessionNotFound, SessionStorageError
from .rewind_models import (
    CoverageToken,
    RewindCoverageRecord,
    RewindGapPrepare,
    RewindMutationPrepare,
    RewindMutationRecord,
    RewindMutationStatus,
)


def _load_coverage(
    connection: sqlite3.Connection, token: CoverageToken
) -> RewindCoverageRecord:
    row = connection.execute(
        "SELECT * FROM workspace_rewind_coverage "
        "WHERE workspace_fingerprint = ?",
        (token.workspace_fingerprint,),
    ).fetchone()
    if row is None:
        raise SessionNotFound("rewind coverage not found")
    record = coverage_record(row)
    _require_coverage_integrity(connection, record)
    if record.token != token:
        raise SessionStorageError("rewind coverage generation moved")
    return record


def _advance_active_coverage(
    connection: sqlite3.Connection,
    request: RewindMutationPrepare,
    sequence: int,
    timestamp: str,
) -> None:
    changed = connection.execute(
        "UPDATE workspace_rewind_coverage "
        "SET mutation_high_water = ?, mutation_count = mutation_count + 1, "
        "updated_at = ? "
        "WHERE workspace_fingerprint = ? AND generation = ? "
        "AND state = 'active' AND mutation_high_water < ?",
        (
            sequence,
            timestamp,
            request.coverage.workspace_fingerprint,
            request.coverage.generation,
            sequence,
        ),
    )
    if changed.rowcount != 1:
        raise SessionStorageError("rewind coverage moved during prepare")


def _require_identity_rows(
    connection: sqlite3.Connection,
    request: RewindMutationPrepare | RewindGapPrepare,
) -> None:
    _require_thread(connection, request.owner_thread_id)
    _require_thread(connection, request.origin_thread_id)
    if request.task_id is not None:
        row = connection.execute(
            "SELECT 1 FROM tasks WHERE id = ?", (request.task_id,)
        ).fetchone()
        if row is None:
            raise SessionNotFound("task not found")


def _load_idempotent(
    connection: sqlite3.Connection,
    fingerprint: str,
    origin_thread_id: str,
    request_id: str,
) -> RewindMutationRecord | None:
    row = connection.execute(
        "SELECT mutation_id FROM workspace_mutations "
        "WHERE workspace_fingerprint = ? AND origin_thread_id = ? "
        "AND request_id = ?",
        (fingerprint, origin_thread_id, request_id),
    ).fetchone()
    return None if row is None else _load_by_id(connection, row["mutation_id"])


def _load_by_id(
    connection: sqlite3.Connection, mutation_id: str
) -> RewindMutationRecord:
    row = connection.execute(
        "SELECT * FROM workspace_mutations WHERE mutation_id = ?",
        (mutation_id,),
    ).fetchone()
    if row is None:
        raise SessionNotFound("rewind mutation not found")
    paths = connection.execute(
        "SELECT * FROM workspace_mutation_paths "
        "WHERE mutation_sequence = ? ORDER BY ordinal",
        (row["sequence"],),
    ).fetchall()
    return mutation_record(row, paths)


def _shared_identity(
    record: RewindMutationRecord,
    request: RewindMutationPrepare | RewindGapPrepare,
) -> bool:
    return (
        record.coverage == request.coverage
        and record.owner_thread_id == request.owner_thread_id
        and record.origin_thread_id == request.origin_thread_id
        and record.task_id == request.task_id
        and record.parent_request_id == request.parent_request_id
        and record.request_id == request.request_id
        and record.action_name == request.action_name
    )


def _matches_prepare(
    record: RewindMutationRecord, request: RewindMutationPrepare
) -> bool:
    return (
        record.status is not RewindMutationStatus.GAP
        and _shared_identity(record, request)
        and record.snapshot_handle == request.snapshot_handle
        and record.paths == request.paths
    )


def _matches_gap(record: RewindMutationRecord, request: RewindGapPrepare) -> bool:
    return (
        record.status is RewindMutationStatus.GAP
        and _shared_identity(record, request)
        and record.gap_reason == request.reason
    )
