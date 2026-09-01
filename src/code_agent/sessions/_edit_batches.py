from __future__ import annotations

import sqlite3
import uuid

from ._codec import encode_datetime, utc_now
from ._edit_batch_repository_support import (
    idempotent_batch,
    list_batches,
    require_settlement_ready,
)
from ._edit_batch_rows import load_edit_batch
from ._edit_batch_writes import (
    insert_edit_batch,
    require_workspace_available,
)
from ._records import _text
from ._rewind_codec import encode_rewind_handle
from ._rewind_model_base import (
    MAX_REWIND_PAGE_SIZE,
    CoverageToken,
    bounded_int,
    optional_text,
)
from ._rewind_mutation_sql import (
    _advance_active_coverage,
    _load_coverage,
    _load_idempotent,
    _require_identity_rows,
)
from ._rewind_mutations import _insert_paths, _insert_prepared
from .edit_batch_models import (
    EditBatchOperationProgress,
    EditBatchPrepare,
    EditBatchRecord,
    EditBatchState,
)
from .errors import SessionNotFound, SessionStorageError
from .rewind_models import RewindCoverageState, RewindMutationStatus


_TERMINAL = frozenset(
    {
        EditBatchState.COMPLETED,
        EditBatchState.ROLLED_BACK,
        EditBatchState.CONFLICTED,
    }
)


class EditBatchRepositoryMixin:
    _database: object

    async def prepare_edit_batch(
        self, request: EditBatchPrepare
    ) -> EditBatchRecord:
        if not isinstance(request, EditBatchPrepare):
            raise TypeError("request must be an EditBatchPrepare")
        mutation = request.mutation
        encoded_handle = encode_rewind_handle(mutation.snapshot_handle)
        timestamp = encode_datetime(utc_now())
        mutation_id = uuid.uuid4().hex

        def write(connection: sqlite3.Connection) -> EditBatchRecord:
            coverage = _load_coverage(connection, mutation.coverage)
            if coverage.state is not RewindCoverageState.ACTIVE:
                raise SessionStorageError("rewind coverage is not active")
            existing = _load_idempotent(
                connection,
                mutation.coverage.workspace_fingerprint,
                mutation.origin_thread_id,
                mutation.request_id,
            )
            if existing is not None:
                return idempotent_batch(connection, existing, request)
            require_workspace_available(
                connection, mutation.coverage.workspace_fingerprint
            )
            _require_identity_rows(connection, mutation)
            sequence = _insert_prepared(
                connection, mutation, mutation_id, encoded_handle, timestamp
            )
            _insert_paths(connection, sequence, mutation)
            insert_edit_batch(connection, sequence, request, timestamp)
            _advance_active_coverage(connection, mutation, sequence, timestamp)
            return load_edit_batch(connection, mutation_id)

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def get_edit_batch(self, mutation_id: str) -> EditBatchRecord:
        mutation_id = _text(mutation_id, "mutation_id")
        return await self._database.read(  # type: ignore[attr-defined]
            lambda connection: load_edit_batch(connection, mutation_id)
        )

    async def list_edit_batches(
        self, workspace_fingerprint: str, *, limit: int = MAX_REWIND_PAGE_SIZE
    ) -> tuple[EditBatchRecord, ...]:
        fingerprint = CoverageToken(
            workspace_fingerprint, 1
        ).workspace_fingerprint
        bounded_int(limit, "limit", minimum=1, maximum=MAX_REWIND_PAGE_SIZE)
        return await self._database.read(  # type: ignore[attr-defined]
            lambda connection: list_batches(
                connection, fingerprint, limit, unresolved=False
            )
        )

    async def list_unresolved_edit_batches(
        self, workspace_fingerprint: str
    ) -> tuple[EditBatchRecord, ...]:
        fingerprint = CoverageToken(
            workspace_fingerprint, 1
        ).workspace_fingerprint
        return await self._database.read(  # type: ignore[attr-defined]
            lambda connection: list_batches(
                connection, fingerprint, 1, unresolved=True
            )
        )

    async def transition_edit_batch(
        self, mutation_id: str, target: EditBatchState
    ) -> EditBatchRecord:
        mutation_id = _text(mutation_id, "mutation_id")
        if not isinstance(target, EditBatchState):
            raise TypeError("target must be an EditBatchState")
        if target not in {EditBatchState.APPLYING, EditBatchState.ROLLING_BACK}:
            raise ValueError("target must be applying or rolling_back")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> EditBatchRecord:
            current = load_edit_batch(connection, mutation_id)
            if current.state is target:
                return current
            allowed = (
                {EditBatchState.PREPARED}
                if target is EditBatchState.APPLYING
                else {EditBatchState.PREPARED, EditBatchState.APPLYING}
            )
            if current.state not in allowed:
                raise SessionStorageError("edit batch transition is invalid")
            changed = connection.execute(
                "UPDATE workspace_edit_batches SET state = ?, updated_at = ? "
                "WHERE mutation_sequence = ? AND state = ?",
                (
                    target.value,
                    timestamp,
                    current.mutation.sequence,
                    current.state.value,
                ),
            )
            if changed.rowcount != 1:
                raise SessionStorageError("edit batch state moved")
            return load_edit_batch(connection, mutation_id)

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def commit_edit_batch_operation(
        self, mutation_id: str, ordinal: int
    ) -> EditBatchRecord:
        mutation_id = _text(mutation_id, "mutation_id")
        bounded_int(ordinal, "ordinal")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> EditBatchRecord:
            current = load_edit_batch(connection, mutation_id)
            if ordinal >= len(current.operations):
                raise SessionNotFound("edit batch operation not found")
            operation = current.operations[ordinal]
            if operation.progress is EditBatchOperationProgress.COMMITTED:
                return current
            if current.state is not EditBatchState.APPLYING:
                raise SessionStorageError("edit batch is not applying")
            if any(
                item.progress is not EditBatchOperationProgress.COMMITTED
                for item in current.operations[:ordinal]
            ):
                raise SessionStorageError("edit batch operation order moved")
            changed = connection.execute(
                "UPDATE workspace_edit_batch_operations "
                "SET progress = 'committed', committed_at = ? "
                "WHERE mutation_sequence = ? AND ordinal = ? "
                "AND progress = 'pending'",
                (timestamp, current.mutation.sequence, ordinal),
            )
            if changed.rowcount != 1:
                raise SessionStorageError("edit batch operation progress moved")
            connection.execute(
                "UPDATE workspace_edit_batches SET updated_at = ? "
                "WHERE mutation_sequence = ?",
                (timestamp, current.mutation.sequence),
            )
            return load_edit_batch(connection, mutation_id)

        return await self._database.write(write)  # type: ignore[attr-defined]

    async def settle_edit_batch(
        self,
        mutation_id: str,
        target: EditBatchState,
        *,
        conflict_code: str | None = None,
    ) -> EditBatchRecord:
        mutation_id = _text(mutation_id, "mutation_id")
        if not isinstance(target, EditBatchState):
            raise TypeError("target must be an EditBatchState")
        if target not in _TERMINAL:
            raise ValueError("target must be a terminal edit batch state")
        conflict = optional_text(conflict_code, "conflict_code")
        if (target is EditBatchState.CONFLICTED) != (conflict is not None):
            raise ValueError("conflicted settlement requires only a conflict_code")
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> EditBatchRecord:
            current = load_edit_batch(connection, mutation_id)
            if current.state is target:
                if current.conflict_code != conflict:
                    raise SessionStorageError("edit batch settlement drifted")
                return current
            if current.state in _TERMINAL:
                raise SessionStorageError("edit batch has a different terminal state")
            require_settlement_ready(current, target)
            parent_status = (
                RewindMutationStatus.COMPLETED
                if target is EditBatchState.COMPLETED
                else RewindMutationStatus.ABORTED
            )
            batch = connection.execute(
                "UPDATE workspace_edit_batches SET state = ?, conflict_code = ?, "
                "updated_at = ?, settled_at = ? WHERE mutation_sequence = ? "
                "AND state = ?",
                (
                    target.value,
                    conflict,
                    timestamp,
                    timestamp,
                    current.mutation.sequence,
                    current.state.value,
                ),
            )
            parent = connection.execute(
                "UPDATE workspace_mutations SET status = ?, completed_at = ? "
                "WHERE sequence = ? AND mutation_id = ? AND status = 'prepared'",
                (
                    parent_status.value,
                    timestamp,
                    current.mutation.sequence,
                    mutation_id,
                ),
            )
            if batch.rowcount != 1 or parent.rowcount != 1:
                raise SessionStorageError("edit batch settlement moved")
            return load_edit_batch(connection, mutation_id)

        return await self._database.write(write)  # type: ignore[attr-defined]
