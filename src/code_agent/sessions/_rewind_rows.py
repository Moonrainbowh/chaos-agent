from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from ._codec import decode_datetime, decode_metadata
from ._rewind_codec import decode_rewind_handle
from .errors import SessionCorruptionError
from .models import CheckpointRecord
from .rewind_models import (
    CoverageToken,
    RewindBaseline,
    RewindCheckpointFact,
    RewindCoverageRecord,
    RewindCoverageState,
    RewindMutationPath,
    RewindMutationRecord,
    RewindMutationStatus,
)


def coverage_record(row: sqlite3.Row) -> RewindCoverageRecord:
    try:
        return RewindCoverageRecord(
            CoverageToken(row["workspace_fingerprint"], row["generation"]),
            RewindCoverageState(row["state"]),
            row["mutation_high_water"],
            row["invalidation_reason"],
        )
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid rewind coverage record") from error


def mutation_path(row: sqlite3.Row) -> RewindMutationPath:
    try:
        pre = row["pre_existed"]
        post = row["post_existed"]
        if pre not in (0, 1) or post not in (0, 1):
            raise ValueError("invalid persisted existence flag")
        return RewindMutationPath(
            row["path"],
            bool(pre),
            row["pre_sha256"],
            RewindBaseline(row["baseline"]),
            bool(post),
            row["post_sha256"],
        )
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid rewind mutation path") from error


def mutation_record(
    row: sqlite3.Row,
    path_rows: Sequence[sqlite3.Row],
) -> RewindMutationRecord:
    try:
        handle = row["snapshot_handle"]
        return RewindMutationRecord(
            row["mutation_id"],
            row["sequence"],
            CoverageToken(
                row["workspace_fingerprint"], row["coverage_generation"]
            ),
            row["owner_thread_id"],
            row["origin_thread_id"],
            row["task_id"],
            row["parent_request_id"],
            row["request_id"],
            row["action_name"],
            RewindMutationStatus(row["status"]),
            row["gap_reason"],
            None if handle is None else decode_rewind_handle(handle),
            tuple(mutation_path(item) for item in path_rows),
            decode_datetime(row["created_at"], "rewind mutation"),
            (
                None
                if row["completed_at"] is None
                else decode_datetime(row["completed_at"], "rewind mutation")
            ),
        )
    except SessionCorruptionError:
        raise
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid rewind mutation record") from error


def checkpoint_record(row: sqlite3.Row) -> CheckpointRecord:
    try:
        return CheckpointRecord(
            id=row["id"],
            thread_id=row["thread_id"],
            label=row["label"],
            metadata=decode_metadata(row["metadata"]),
            created_at=decode_datetime(row["created_at"], "checkpoint"),
            message_sequence=row["message_sequence"],
            event_sequence=row["event_sequence"],
        )
    except SessionCorruptionError:
        raise
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted checkpoint") from error


def checkpoint_fact(row: sqlite3.Row) -> RewindCheckpointFact | None:
    try:
        owner = row["rewind_owner_thread_id"]
        if owner is None:
            return None
        return RewindCheckpointFact(
            row["id"],
            owner,
            CoverageToken(
                row["workspace_fingerprint"], row["coverage_generation"]
            ),
            row["rewind_mutation_sequence"],
            RewindCoverageState(row["rewind_coverage_state"]),
            decode_datetime(row["rewind_created_at"], "checkpoint rewind fact"),
        )
    except SessionCorruptionError:
        raise
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid checkpoint rewind fact") from error
