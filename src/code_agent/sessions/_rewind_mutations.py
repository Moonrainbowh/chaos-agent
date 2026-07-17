from __future__ import annotations

import sqlite3
import uuid

from ._codec import encode_datetime, utc_now
from ._records import _text
from ._rewind_codec import encode_rewind_handle
from ._rewind_mutation_sql import (
    _load_by_id,
    _load_coverage,
    _load_idempotent,
    _matches_gap,
    _matches_prepare,
    _require_coverage_high_water,
    _require_identity_rows,
)
from ._rewind_rows import coverage_record
from .errors import SessionStorageError
from .rewind_models import (
    CoverageToken,
    RewindCoverageRecord,
    RewindCoverageState,
    RewindGapPrepare,
    RewindMutationPrepare,
    RewindMutationRecord,
    RewindMutationStatus,
)


def _insert_prepared(
    connection: sqlite3.Connection,
    request: RewindMutationPrepare,
    mutation_id: str,
    encoded_handle: str,
    timestamp: str,
) -> int:
    cursor = connection.execute(
        "INSERT INTO workspace_mutations("
        "mutation_id, workspace_fingerprint, coverage_generation, "
        "owner_thread_id, origin_thread_id, task_id, parent_request_id, "
        "request_id, action_name, path_count, status, gap_reason, snapshot_handle, "
        "created_at, completed_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', NULL, ?, ?, NULL)",
        (
            mutation_id,
            request.coverage.workspace_fingerprint,
            request.coverage.generation,
            request.owner_thread_id,
            request.origin_thread_id,
            request.task_id,
            request.parent_request_id,
            request.request_id,
            request.action_name,
            len(request.paths),
            encoded_handle,
            timestamp,
        ),
    )
    return int(cursor.lastrowid)


def _insert_paths(
    connection: sqlite3.Connection,
    sequence: int,
    request: RewindMutationPrepare,
) -> None:
    for ordinal, path in enumerate(request.paths):
        connection.execute(
            "INSERT INTO workspace_mutation_paths VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                ordinal,
                path.path,
                int(path.before_existed),
                path.before_sha256,
                path.baseline.value,
                int(path.after_existed),
                path.after_sha256,
            ),
        )


def _advance_active_coverage(
    connection: sqlite3.Connection,
    request: RewindMutationPrepare,
    sequence: int,
    timestamp: str,
) -> None:
    changed = connection.execute(
        "UPDATE workspace_rewind_coverage "
        "SET mutation_high_water = ?, updated_at = ? "
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


def _insert_gap(
    connection: sqlite3.Connection,
    request: RewindGapPrepare,
    mutation_id: str,
    timestamp: str,
) -> int:
    cursor = connection.execute(
        "INSERT INTO workspace_mutations("
        "mutation_id, workspace_fingerprint, coverage_generation, "
        "owner_thread_id, origin_thread_id, task_id, parent_request_id, "
        "request_id, action_name, path_count, status, gap_reason, snapshot_handle, "
        "created_at, completed_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'gap', ?, NULL, ?, ?)",
        (
            mutation_id,
            request.coverage.workspace_fingerprint,
            request.coverage.generation,
            request.owner_thread_id,
            request.origin_thread_id,
            request.task_id,
            request.parent_request_id,
            request.request_id,
            request.action_name,
            request.reason,
            timestamp,
            timestamp,
        ),
    )
    return int(cursor.lastrowid)


def _invalidate_coverage(
    connection: sqlite3.Connection,
    request: RewindGapPrepare,
    sequence: int,
    timestamp: str,
) -> None:
    changed = connection.execute(
        "UPDATE workspace_rewind_coverage SET state = 'invalidated', "
        "invalidation_reason = COALESCE(invalidation_reason, ?), "
        "mutation_high_water = ?, updated_at = ? "
        "WHERE workspace_fingerprint = ? AND generation = ?",
        (
            request.reason,
            sequence,
            timestamp,
            request.coverage.workspace_fingerprint,
            request.coverage.generation,
        ),
    )
    if changed.rowcount != 1:
        raise SessionStorageError("rewind coverage generation moved")


class RewindMutationRepositoryMixin:
    _database: object

    async def ensure_rewind_coverage(
        self, workspace_fingerprint: str
    ) -> RewindCoverageRecord:
        fingerprint = CoverageToken(workspace_fingerprint, 1).workspace_fingerprint
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> RewindCoverageRecord:
            connection.execute(
                "INSERT OR IGNORE INTO workspace_rewind_coverage("
                "workspace_fingerprint, generation, state, mutation_high_water, "
                "invalidation_reason, started_at, updated_at"
                ") VALUES (?, 1, 'active', 0, NULL, ?, ?)",
                (fingerprint, timestamp, timestamp),
            )
            row = connection.execute(
                "SELECT * FROM workspace_rewind_coverage "
                "WHERE workspace_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if row is None:
                raise SessionStorageError("rewind coverage was not persisted")
            record = coverage_record(row)
            _require_coverage_high_water(connection, record)
            return record

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def prepare_rewind_mutation(
        self, request: RewindMutationPrepare
    ) -> RewindMutationRecord:
        if not isinstance(request, RewindMutationPrepare):
            raise TypeError("request must be a RewindMutationPrepare")
        encoded_handle = encode_rewind_handle(request.snapshot_handle)
        timestamp = encode_datetime(utc_now())
        mutation_id = uuid.uuid4().hex

        def write(connection: sqlite3.Connection) -> RewindMutationRecord:
            coverage = _load_coverage(connection, request.coverage)
            if coverage.state is not RewindCoverageState.ACTIVE:
                raise SessionStorageError("rewind coverage is not active")
            existing = _load_idempotent(
                connection,
                request.coverage.workspace_fingerprint,
                request.origin_thread_id,
                request.request_id,
            )
            if existing is not None:
                if not _matches_prepare(existing, request):
                    raise SessionStorageError("rewind idempotency key was reused")
                return existing
            _require_identity_rows(connection, request)
            sequence = _insert_prepared(
                connection, request, mutation_id, encoded_handle, timestamp
            )
            _insert_paths(connection, sequence, request)
            _advance_active_coverage(connection, request, sequence, timestamp)
            return _load_by_id(connection, mutation_id)

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def complete_rewind_mutation(
        self, mutation_id: str
    ) -> RewindMutationRecord:
        return await self._finish_mutation(mutation_id, RewindMutationStatus.COMPLETED)

    async def abort_rewind_mutation(
        self, mutation_id: str
    ) -> RewindMutationRecord:
        return await self._finish_mutation(mutation_id, RewindMutationStatus.ABORTED)

    async def _finish_mutation(
        self,
        mutation_id: str,
        status: RewindMutationStatus,
    ) -> RewindMutationRecord:
        mutation_id = _text(mutation_id, "mutation_id")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> RewindMutationRecord:
            current = _load_by_id(connection, mutation_id)
            if current.status is not RewindMutationStatus.PREPARED:
                raise SessionStorageError("rewind mutation is not prepared")
            _load_coverage(connection, current.coverage)
            changed = connection.execute(
                "UPDATE workspace_mutations SET status = ?, completed_at = ? "
                "WHERE mutation_id = ? AND status = 'prepared' "
                "AND workspace_fingerprint = ? AND coverage_generation = ? "
                "AND owner_thread_id = ? AND origin_thread_id = ? "
                "AND task_id IS ? AND parent_request_id IS ? "
                "AND request_id = ? AND action_name = ?",
                (
                    status.value,
                    timestamp,
                    mutation_id,
                    current.coverage.workspace_fingerprint,
                    current.coverage.generation,
                    current.owner_thread_id,
                    current.origin_thread_id,
                    current.task_id,
                    current.parent_request_id,
                    current.request_id,
                    current.action_name,
                ),
            )
            if changed.rowcount != 1:
                raise SessionStorageError("rewind mutation identity moved")
            return _load_by_id(connection, mutation_id)

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def record_rewind_gap(
        self, request: RewindGapPrepare
    ) -> RewindMutationRecord:
        if not isinstance(request, RewindGapPrepare):
            raise TypeError("request must be a RewindGapPrepare")
        timestamp = encode_datetime(utc_now())
        mutation_id = uuid.uuid4().hex

        def write(connection: sqlite3.Connection) -> RewindMutationRecord:
            _load_coverage(connection, request.coverage)
            existing = _load_idempotent(
                connection,
                request.coverage.workspace_fingerprint,
                request.origin_thread_id,
                request.request_id,
            )
            if existing is not None:
                if not _matches_gap(existing, request):
                    raise SessionStorageError("rewind idempotency key was reused")
                return existing
            _require_identity_rows(connection, request)
            sequence = _insert_gap(connection, request, mutation_id, timestamp)
            _invalidate_coverage(connection, request, sequence, timestamp)
            return _load_by_id(connection, mutation_id)

        return await self._database.write(write)  # type: ignore[attr-defined]
