from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from ._codec import decode_datetime
from ._records import _require_thread, _text, _thread_maximum
from ._rewind_codec import decode_rewind_cursor, encode_rewind_cursor
from ._rewind_rows import (
    checkpoint_fact,
    checkpoint_record,
    coverage_record,
    mutation_record,
)
from .errors import SessionCorruptionError, SessionNotFound
from .rewind_models import (
    CoverageToken, MAX_REWIND_PAGE_SIZE,
    RewindCandidate, RewindCandidatePage, RewindCheckpointFact,
    RewindCoverageRecord, RewindMutationRecord, RewindObservation,
    RewindObservationHeads, RewindReadLimits,
)


def _load_checkpoint(
    connection: sqlite3.Connection,
    thread_id: str,
    checkpoint_id: str,
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT c.*, f.owner_thread_id AS rewind_owner_thread_id, "
        "f.workspace_fingerprint, f.coverage_generation, "
        "f.mutation_sequence AS rewind_mutation_sequence, "
        "f.coverage_state AS rewind_coverage_state, "
        "f.created_at AS rewind_created_at "
        "FROM checkpoints AS c LEFT JOIN checkpoint_rewind_facts AS f "
        "ON f.checkpoint_id = c.id "
        "WHERE c.id = ? AND c.thread_id = ?",
        (checkpoint_id, thread_id),
    ).fetchone()
    if row is None:
        raise SessionNotFound("checkpoint not found")
    return row


def _conversation_count(
    connection: sqlite3.Connection,
    thread_id: str,
    bound: int | None,
    head: int,
) -> int | None:
    if bound is None or bound > head:
        return None
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM messages WHERE thread_id = ? "
            "AND sequence > ? AND sequence <= ?",
            (thread_id, bound, head),
        ).fetchone()[0]
    )


def _mutation_head(
    connection: sqlite3.Connection,
    workspace_fingerprint: str,
) -> int:
    return int(
        connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) "
            "FROM workspace_mutations WHERE workspace_fingerprint = ?",
            (workspace_fingerprint,),
        ).fetchone()[0]
    )


def _load_coverage(
    connection: sqlite3.Connection,
    workspace_fingerprint: str,
) -> RewindCoverageRecord | None:
    row = connection.execute(
        "SELECT * FROM workspace_rewind_coverage "
        "WHERE workspace_fingerprint = ?",
        (workspace_fingerprint,),
    ).fetchone()
    return None if row is None else coverage_record(row)


def _heads(
    connection: sqlite3.Connection,
    thread_id: str,
    workspace_fingerprint: str | None,
    *,
    require_coverage: bool = False,
) -> RewindObservationHeads:
    message_head = _thread_maximum(connection, "messages", thread_id)
    event_head = _thread_maximum(connection, "events", thread_id)
    if workspace_fingerprint is None:
        return RewindObservationHeads(message_head, event_head, 0, None, None)
    coverage = _load_coverage(connection, workspace_fingerprint)
    if coverage is None:
        if require_coverage:
            raise SessionCorruptionError("checkpoint coverage is missing")
        return RewindObservationHeads(message_head, event_head, 0, None, None)
    return RewindObservationHeads(
        message_head,
        event_head,
        _mutation_head(connection, workspace_fingerprint),
        coverage.generation,
        coverage.state,
    )


def _load_mutation_rows(
    connection: sqlite3.Connection,
    fact: RewindCheckpointFact,
    head: int,
    limit: int,
) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM workspace_mutations "
        "WHERE workspace_fingerprint = ? AND coverage_generation = ? "
        "AND sequence > ? AND sequence <= ? ORDER BY sequence LIMIT ?",
        (
            fact.workspace_fingerprint,
            fact.generation,
            fact.mutation_sequence,
            head,
            limit + 1,
        ),
    ).fetchall()


def _load_path_rows(
    connection: sqlite3.Connection,
    sequences: Sequence[int],
    limit: int,
) -> list[sqlite3.Row]:
    if not sequences:
        return []
    marks = ",".join("?" for _ in sequences)
    return connection.execute(
        "SELECT * FROM workspace_mutation_paths "
        f"WHERE mutation_sequence IN ({marks}) "
        "ORDER BY mutation_sequence, ordinal LIMIT ?",
        (*sequences, limit + 1),
    ).fetchall()


def _decode_mutations(
    mutation_rows: Sequence[sqlite3.Row],
    path_rows: Sequence[sqlite3.Row],
) -> tuple[RewindMutationRecord, ...]:
    grouped = {
        row["sequence"]: tuple(
            path
            for path in path_rows
            if path["mutation_sequence"] == row["sequence"]
        )
        for row in mutation_rows
    }
    return tuple(
        mutation_record(row, grouped[row["sequence"]]) for row in mutation_rows
    )


def _observation(
    connection: sqlite3.Connection,
    thread_id: str,
    checkpoint_id: str,
    limits: RewindReadLimits,
) -> RewindObservation:
    row = _load_checkpoint(connection, thread_id, checkpoint_id)
    checkpoint = checkpoint_record(row)
    fact = checkpoint_fact(row)
    heads = _heads(
        connection,
        thread_id,
        None if fact is None else fact.workspace_fingerprint,
        require_coverage=fact is not None,
    )
    count = _conversation_count(
        connection, thread_id, checkpoint.message_sequence, heads.message_sequence
    )
    if fact is None:
        return RewindObservation(checkpoint, None, count, heads, (), False)
    mutation_rows = _load_mutation_rows(
        connection, fact, heads.mutation_sequence, limits.max_mutations
    )
    if len(mutation_rows) > limits.max_mutations:
        return RewindObservation(checkpoint, fact, count, heads, (), True)
    sequences = tuple(row["sequence"] for row in mutation_rows)
    path_rows = _load_path_rows(connection, sequences, limits.max_paths)
    if len(path_rows) > limits.max_paths:
        return RewindObservation(checkpoint, fact, count, heads, (), True)
    mutations = _decode_mutations(mutation_rows, path_rows)
    return RewindObservation(checkpoint, fact, count, heads, mutations, False)


def _candidate_rows(
    connection: sqlite3.Connection,
    thread_id: str,
    cursor: tuple[str, str] | None,
    limit: int,
) -> list[sqlite3.Row]:
    predicate = ""
    parameters: list[object] = [thread_id]
    if cursor is not None:
        predicate = (
            "AND (c.created_at < ? OR "
            "(c.created_at = ? AND c.id < ?)) "
        )
        parameters.extend((cursor[0], cursor[0], cursor[1]))
    parameters.append(limit + 1)
    return connection.execute(
        "SELECT c.id, c.label, c.created_at, c.message_sequence, "
        "f.checkpoint_id AS rewind_checkpoint_id "
        "FROM checkpoints AS c LEFT JOIN checkpoint_rewind_facts AS f "
        "ON f.checkpoint_id = c.id WHERE c.thread_id = ? "
        f"{predicate}ORDER BY c.created_at DESC, c.id DESC LIMIT ?",
        tuple(parameters),
    ).fetchall()


def _candidate_items(rows: Sequence[sqlite3.Row]) -> tuple[RewindCandidate, ...]:
    try:
        return tuple(
            RewindCandidate(
                row["id"],
                row["label"],
                decode_datetime(row["created_at"], "checkpoint"),
                row["message_sequence"] is not None,
                row["rewind_checkpoint_id"] is not None,
            )
            for row in rows
        )
    except (TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid rewind candidate") from error


class RewindObservationRepositoryMixin:
    _database: object

    async def observe_rewind(
        self,
        thread_id: str,
        checkpoint_id: str,
        limits: RewindReadLimits,
    ) -> RewindObservation:
        thread_id = _text(thread_id, "thread_id")
        checkpoint_id = _text(checkpoint_id, "checkpoint_id")
        if not isinstance(limits, RewindReadLimits):
            raise TypeError("limits must be RewindReadLimits")

        def read(connection: sqlite3.Connection) -> RewindObservation:
            return _observation(connection, thread_id, checkpoint_id, limits)

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def observe_rewind_heads(
        self,
        thread_id: str,
        workspace_fingerprint: str,
    ) -> RewindObservationHeads:
        thread_id = _text(thread_id, "thread_id")
        fingerprint = CoverageToken(
            workspace_fingerprint, 1
        ).workspace_fingerprint

        def read(connection: sqlite3.Connection) -> RewindObservationHeads:
            _require_thread(connection, thread_id)
            return _heads(connection, thread_id, fingerprint)

        return await self._database.read(read)  # type: ignore[attr-defined]

    async def list_rewind_candidates(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> RewindCandidatePage:
        thread_id = _text(thread_id, "thread_id")
        if type(limit) is not int or not 1 <= limit <= MAX_REWIND_PAGE_SIZE:
            raise ValueError("limit must be between 1 and 100")
        decoded = None if cursor is None else decode_rewind_cursor(cursor)

        def read(connection: sqlite3.Connection) -> RewindCandidatePage:
            _require_thread(connection, thread_id)
            rows = _candidate_rows(connection, thread_id, decoded, limit)
            visible = rows[:limit]
            items = _candidate_items(visible)
            next_cursor = None
            if len(rows) > limit:
                last = visible[-1]
                next_cursor = encode_rewind_cursor(
                    last["created_at"], last["id"]
                )
            return RewindCandidatePage(items, next_cursor)

        return await self._database.read(read)  # type: ignore[attr-defined]
