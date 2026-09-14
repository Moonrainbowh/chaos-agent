from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from ._edit_batch_rows import edit_batch_record, load_edit_batch
from ._edit_batch_writes import batch_matches
from ._rewind_model_base import canonical_path
from ._rewind_mutation_sql import _matches_prepare
from .edit_batch_models import (
    EditBatchOperationProgress,
    EditBatchPrepare,
    EditBatchRecord,
    EditBatchState,
)
from .errors import SessionNotFound, SessionStorageError
from .rewind_models import RewindMutationRecord


_UNRESOLVED_VALUES = (
    EditBatchState.PREPARED.value,
    EditBatchState.APPLYING.value,
    EditBatchState.ROLLING_BACK.value,
    EditBatchState.CONFLICTED.value,
)


def idempotent_batch(
    connection: sqlite3.Connection,
    mutation: RewindMutationRecord,
    request: EditBatchPrepare,
) -> EditBatchRecord:
    if not _matches_prepare(mutation, request.mutation):
        raise SessionStorageError("edit batch idempotency key was reused")
    try:
        record = load_edit_batch(connection, mutation.mutation_id)
    except SessionNotFound as error:
        raise SessionStorageError("edit batch idempotency key was reused") from error
    if not batch_matches(record, request):
        raise SessionStorageError("edit batch idempotency key was reused")
    return record


def post_identity_map(
    identities: Mapping[str, tuple[int, int] | None],
) -> dict[str, tuple[int, int] | None]:
    """Validate and canonicalise captured post-apply ownership proofs."""
    if not isinstance(identities, Mapping):
        raise TypeError("identities must be a mapping")
    recorded: dict[str, tuple[int, int] | None] = {}
    for path, identity in identities.items():
        key = canonical_path(path)
        if key in recorded:
            raise ValueError("duplicate post identity path")
        recorded[key] = _identity_pair(identity)
    return recorded


def _identity_pair(identity: object) -> tuple[int, int] | None:
    if identity is None:
        return None
    if not isinstance(identity, tuple) or len(identity) != 2:
        raise TypeError("post identity must be a (device, inode) pair or None")
    for value in identity:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("post identity values must be integers")
        if value < 0:
            raise ValueError("post identity values cannot be negative")
    return identity


def require_settlement_ready(
    record: EditBatchRecord, target: EditBatchState
) -> None:
    if target is EditBatchState.COMPLETED:
        if record.state is not EditBatchState.APPLYING or any(
            item.progress is not EditBatchOperationProgress.COMMITTED
            for item in record.operations
        ):
            raise SessionStorageError("edit batch is not ready to complete")
    elif target is EditBatchState.ROLLED_BACK and record.state not in {
        EditBatchState.PREPARED,
        EditBatchState.APPLYING,
        EditBatchState.ROLLING_BACK,
    }:
        raise SessionStorageError("edit batch is not ready to roll back")


def list_batches(
    connection: sqlite3.Connection,
    fingerprint: str,
    limit: int,
    *,
    unresolved: bool,
) -> tuple[EditBatchRecord, ...]:
    where = " AND b.state IN (?, ?, ?, ?)" if unresolved else ""
    arguments = (
        (fingerprint, *_UNRESOLVED_VALUES, limit)
        if unresolved
        else (fingerprint, limit)
    )
    rows = connection.execute(
        "SELECT b.*, m.mutation_id FROM workspace_edit_batches b "
        "JOIN workspace_mutations m ON m.sequence = b.mutation_sequence "
        "WHERE b.workspace_fingerprint = ?" + where + " "
        "ORDER BY b.mutation_sequence DESC LIMIT ?",
        arguments,
    ).fetchall()
    return tuple(edit_batch_record(connection, row) for row in rows)
