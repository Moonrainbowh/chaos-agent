from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import cast

from code_agent.core._json import JSONValue, plain
from code_agent.workspace._snapshot_manifest import SnapshotManifestEntry

from ._codec import decode_datetime, encode_datetime
from .errors import SessionCorruptionError
from .workspace_models import (
    CheckpointCursor,
    RewindMode,
    RewindOperationRecord,
    RewindOperationStatus,
    WorkspaceLineageRecord,
    WorkspaceLineageStatus,
    WorkspaceSnapshotRecord,
    WorkspaceSnapshotStatus,
)
from ._workspace_model_values import MAX_JSON_BYTES, require_uuid, validate_json_value


def encode_json(value: JSONValue) -> str:
    return json.dumps(
        plain(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def decode_json(payload: object, label: str) -> JSONValue:
    if not isinstance(payload, str):
        raise SessionCorruptionError(f"persisted {label} is not text")
    try:
        if len(payload.encode("utf-8")) > MAX_JSON_BYTES:
            raise ValueError("JSON payload exceeds storage limit")
        value = json.loads(payload)
        validate_json_value(value, label)
        return cast(JSONValue, value)
    except (MemoryError, RecursionError, TypeError, UnicodeError, ValueError) as error:
        raise SessionCorruptionError(f"invalid persisted {label} JSON") from error


def lineage_from_row(row: sqlite3.Row) -> WorkspaceLineageRecord:
    try:
        return WorkspaceLineageRecord(
            row["id"],
            row["repository_id"],
            row["source_root"],
            row["worktree_root"],
            row["branch_name"],
            row["head_commit"],
            row["owner_task_id"],
            WorkspaceLineageStatus(row["status"]),
            decode_datetime(row["created_at"], "lineage"),
            decode_datetime(row["updated_at"], "lineage"),
        )
    except SessionCorruptionError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted workspace lineage") from error


def snapshot_from_rows(
    row: sqlite3.Row, entries: Sequence[sqlite3.Row]
) -> WorkspaceSnapshotRecord:
    try:
        values = tuple(
            SnapshotManifestEntry(
                item["relative_path"],
                _stored_bool(item["existed"], "snapshot existed"),
                item["blob_sha256"],
                item["size"],
                item["mode"],
            )
            for item in entries
        )
        return WorkspaceSnapshotRecord(
            row["id"],
            row["lineage_id"],
            values,
            row["inventory_digest"],
            row["total_bytes"],
            decode_datetime(row["created_at"], "workspace snapshot"),
        )
    except SessionCorruptionError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted workspace snapshot") from error


def cursor_from_row(row: sqlite3.Row) -> CheckpointCursor:
    try:
        lineage_id = row["lineage_id"]
        if lineage_id is None:
            raise SessionCorruptionError("legacy checkpoint is not rewindable")
        goals = decode_json(row["goals_payload"], "checkpoint goals")
        state = decode_json(row["task_state_payload"], "checkpoint task state")
        budget = decode_json(row["budget_payload"], "checkpoint budget")
        if not isinstance(goals, list) or not isinstance(state, dict) or not isinstance(budget, dict):
            raise SessionCorruptionError("invalid checkpoint cursor payload shape")
        return CheckpointCursor(
            row["message_sequence"],
            row["event_sequence"],
            tuple(goals),
            state,
            budget,
            WorkspaceSnapshotStatus(row["snapshot_status"]),
            lineage_id,
        )
    except SessionCorruptionError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted checkpoint cursor") from error


def operation_from_row(row: sqlite3.Row) -> RewindOperationRecord:
    try:
        return RewindOperationRecord(
            row["id"],
            row["lineage_id"],
            row["source_checkpoint_id"],
            row["rollback_checkpoint_id"],
            RewindMode(row["mode"]),
            row["preview_fingerprint"],
            RewindOperationStatus(row["status"]),
            row["error_code"],
            row["replacement_task_id"],
            decode_datetime(row["created_at"], "rewind operation"),
            decode_datetime(row["updated_at"], "rewind operation"),
        )
    except SessionCorruptionError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted rewind operation") from error


def insert_lineage(
    connection: sqlite3.Connection, record: WorkspaceLineageRecord
) -> None:
    connection.execute(
        "INSERT INTO workspace_lineages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.id,
            record.repository_id,
            record.source_root,
            record.worktree_root,
            record.branch_name,
            record.head_commit,
            record.owner_task_id,
            record.status.value,
            encode_datetime(record.created_at),
            encode_datetime(record.updated_at),
        ),
    )


def insert_snapshot(
    connection: sqlite3.Connection, record: WorkspaceSnapshotRecord
) -> None:
    connection.execute(
        "INSERT INTO workspace_snapshots VALUES (?, ?, ?, ?, ?)",
        (
            record.id,
            record.lineage_id,
            record.inventory_digest,
            record.total_bytes,
            encode_datetime(record.created_at),
        ),
    )
    connection.executemany(
        "INSERT INTO workspace_snapshot_entries VALUES (?, ?, ?, ?, ?, ?)",
        (
            (
                record.id,
                entry.relative_path,
                int(entry.existed),
                entry.blob_sha256,
                entry.size,
                entry.mode,
            )
            for entry in record.entries
        ),
    )


def insert_cursor(
    connection: sqlite3.Connection,
    checkpoint_id: str,
    snapshot_id: str | None,
    cursor: CheckpointCursor,
) -> None:
    connection.execute(
        "INSERT INTO checkpoint_workspace_state("
        "checkpoint_id, snapshot_id, message_sequence, event_sequence, goals_payload, "
        "task_state_payload, budget_payload, snapshot_status, lineage_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            checkpoint_id,
            snapshot_id,
            cursor.message_sequence,
            cursor.event_sequence,
            encode_json(cast(JSONValue, cursor.goals_payload)),
            encode_json(cast(JSONValue, cursor.task_state_payload)),
            encode_json(cast(JSONValue, cursor.budget_payload)),
            cursor.snapshot_status.value,
            cursor.lineage_id,
        ),
    )


def _stored_bool(value: object, label: str) -> bool:
    if value not in (0, 1) or isinstance(value, bool):
        raise SessionCorruptionError(f"{label} is not a stored boolean")
    return bool(value)
