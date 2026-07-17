from __future__ import annotations

import sqlite3
import uuid
from typing import Mapping

from code_agent.core._json import JSONValue, validate_json_mapping

from ._codec import encode_datetime, encode_metadata, utc_now
from ._records import _require_thread, _text, _thread_maximum, _touch_thread
from ._rewind_mutation_sql import _require_coverage_high_water
from ._rewind_rows import coverage_record
from .errors import SessionNotFound, SessionStorageError
from .rewind_models import (
    CoverageToken, RewindCheckpointAnchor, RewindCoverageRecord,
)


def _load_coverage(
    connection: sqlite3.Connection,
    coverage: CoverageToken,
) -> RewindCoverageRecord:
    row = connection.execute(
        "SELECT * FROM workspace_rewind_coverage "
        "WHERE workspace_fingerprint = ?",
        (coverage.workspace_fingerprint,),
    ).fetchone()
    if row is None:
        raise SessionNotFound("rewind coverage not found")
    record = coverage_record(row)
    _require_coverage_high_water(connection, record)
    if record.token != coverage:
        raise SessionStorageError("rewind coverage generation moved")
    return record


def _require_quiescent(
    connection: sqlite3.Connection,
    workspace_fingerprint: str,
) -> None:
    pending = connection.execute(
        "SELECT 1 FROM workspace_mutations "
        "WHERE workspace_fingerprint = ? AND status = 'prepared' LIMIT 1",
        (workspace_fingerprint,),
    ).fetchone()
    if pending is not None:
        raise SessionStorageError("prepared rewind mutation exists")


def _validate_anchor(
    connection: sqlite3.Connection,
    anchor: RewindCheckpointAnchor,
) -> None:
    current = _load_coverage(connection, anchor.coverage)
    expected = (anchor.coverage_state, anchor.mutation_sequence)
    actual = (current.state, current.mutation_high_water)
    if actual != expected:
        raise SessionStorageError("rewind checkpoint anchor is stale")
    _require_quiescent(connection, anchor.coverage.workspace_fingerprint)


def _write_anchored_checkpoint(
    connection: sqlite3.Connection,
    identifier: str,
    thread_id: str,
    label: str,
    metadata: Mapping[str, JSONValue],
    timestamp: str,
    anchor: RewindCheckpointAnchor,
) -> None:
    _require_thread(connection, thread_id)
    _require_thread(connection, anchor.owner_thread_id)
    _validate_anchor(connection, anchor)
    message_sequence = _thread_maximum(connection, "messages", thread_id)
    event_sequence = _thread_maximum(connection, "events", thread_id)
    connection.execute(
        "INSERT INTO checkpoints("
        "id, thread_id, label, metadata, created_at, "
        "message_sequence, event_sequence"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            identifier, thread_id, label, encode_metadata(metadata), timestamp,
            message_sequence, event_sequence,
        ),
    )
    connection.execute(
        "INSERT INTO checkpoint_rewind_facts("
        "checkpoint_id, owner_thread_id, workspace_fingerprint, "
        "coverage_generation, mutation_sequence, coverage_state, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            identifier,
            anchor.owner_thread_id,
            anchor.coverage.workspace_fingerprint,
            anchor.coverage.generation,
            anchor.mutation_sequence,
            anchor.coverage_state.value,
            timestamp,
        ),
    )
    _touch_thread(connection, thread_id, timestamp)


class RewindCheckpointRepositoryMixin:
    _database: object

    async def get_rewind_checkpoint_anchor(
        self,
        coverage: CoverageToken,
        owner_thread_id: str,
    ) -> RewindCheckpointAnchor:
        if not isinstance(coverage, CoverageToken):
            raise TypeError("coverage must be a CoverageToken")
        owner_thread_id = _text(owner_thread_id, "owner_thread_id")

        def read(connection: sqlite3.Connection) -> RewindCheckpointAnchor:
            _require_thread(connection, owner_thread_id)
            record = _load_coverage(connection, coverage)
            _require_quiescent(connection, coverage.workspace_fingerprint)
            return RewindCheckpointAnchor(
                coverage,
                owner_thread_id,
                record.state,
                record.mutation_high_water,
            )

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def create_checkpoint(
        self,
        thread_id: str,
        label: str,
        metadata: Mapping[str, JSONValue] | None = None,
        *,
        rewind_anchor: RewindCheckpointAnchor | None = None,
    ) -> str:
        if rewind_anchor is None:
            return await super().create_checkpoint(thread_id, label, metadata)
        if not isinstance(rewind_anchor, RewindCheckpointAnchor):
            raise TypeError("rewind_anchor must be a RewindCheckpointAnchor")
        thread_id = _text(thread_id, "thread_id")
        label = _text(label, "label")
        data: Mapping[str, JSONValue] = {} if metadata is None else metadata
        validate_json_mapping(data, "metadata")
        identifier = uuid.uuid4().hex
        timestamp = encode_datetime(utc_now())

        def write(connection: sqlite3.Connection) -> None:
            _write_anchored_checkpoint(
                connection,
                identifier,
                thread_id,
                label,
                data,
                timestamp,
                rewind_anchor,
            )

        await self._database.write(write)  # type: ignore[attr-defined]
        return identifier
