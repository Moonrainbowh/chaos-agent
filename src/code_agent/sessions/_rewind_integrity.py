from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from ._rewind_rows import coverage_record
from .errors import SessionCorruptionError
from .rewind_models import (
    RewindCheckpointFact,
    RewindCoverageRecord,
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


def _journal_bounds(
    connection: sqlite3.Connection,
    workspace_fingerprint: str,
    *,
    through: int | None = None,
) -> tuple[int, int]:
    predicate = "" if through is None else "AND sequence <= ?"
    parameters = (
        (workspace_fingerprint,)
        if through is None
        else (workspace_fingerprint, through)
    )
    row = connection.execute(
        "SELECT COALESCE(MAX(sequence), 0), COUNT(*) "
        "FROM workspace_mutations WHERE workspace_fingerprint = ? "
        f"{predicate}",
        parameters,
    ).fetchone()
    high_water, count = row[0], row[1]
    if type(high_water) is not int or type(count) is not int:
        raise SessionCorruptionError("invalid rewind journal bounds")
    return high_water, count


def _require_coverage_integrity(
    connection: sqlite3.Connection,
    coverage: RewindCoverageRecord,
) -> tuple[int, int]:
    bounds = _journal_bounds(connection, coverage.workspace_fingerprint)
    expected = (coverage.mutation_high_water, coverage.mutation_count)
    if bounds != expected:
        raise SessionCorruptionError("rewind coverage journal is inconsistent")
    return bounds


def _require_checkpoint_boundary(
    connection: sqlite3.Connection,
    fact: RewindCheckpointFact,
    coverage: RewindCoverageRecord,
) -> int:
    if (
        fact.generation != coverage.generation
        or coverage.mutation_count < fact.mutation_count
    ):
        raise SessionCorruptionError("checkpoint rewind boundary is inconsistent")
    bounds = _journal_bounds(
        connection, fact.workspace_fingerprint, through=fact.mutation_sequence
    )
    if bounds != (fact.mutation_sequence, fact.mutation_count):
        raise SessionCorruptionError("checkpoint rewind boundary is inconsistent")
    return coverage.mutation_count - fact.mutation_count


def _require_mutation_chain(
    rows: Sequence[sqlite3.Row],
    fact: RewindCheckpointFact,
    expected_count: int,
    *,
    limited: bool,
) -> None:
    if any(
        type(row["coverage_generation"]) is not int
        or row["coverage_generation"] != fact.generation
        for row in rows
    ):
        raise SessionCorruptionError("rewind mutation generation is inconsistent")
    if (limited and expected_count < len(rows)) or (
        not limited and expected_count != len(rows)
    ):
        raise SessionCorruptionError("rewind mutation chain is incomplete")


def _require_checkpoint_markers(rows: Sequence[sqlite3.Row]) -> None:
    if any(
        (row["rewind_checkpoint_id"] is None)
        != (row["rewind_expected_checkpoint_id"] is None)
        for row in rows
    ):
        raise SessionCorruptionError("rewind checkpoint marker is inconsistent")
