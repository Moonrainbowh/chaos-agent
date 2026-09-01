from __future__ import annotations

import re
import sqlite3

from .errors import SessionCorruptionError


_FOREIGN_KEYS = {
    "workspace_lineages": {("owner_task_id", "tasks", "id", "NO ACTION")},
    "tasks": {("workspace_lineage_id", "workspace_lineages", "id", "NO ACTION")},
    "workspace_snapshots": {("lineage_id", "workspace_lineages", "id", "NO ACTION")},
    "workspace_snapshot_entries": {
        ("snapshot_id", "workspace_snapshots", "id", "CASCADE")
    },
    "checkpoint_workspace_state": {
        ("checkpoint_id", "checkpoints", "id", "CASCADE"),
        ("snapshot_id", "workspace_snapshots", "id", "NO ACTION"),
        ("lineage_id", "workspace_lineages", "id", "NO ACTION"),
    },
    "rewind_operations": {
        ("lineage_id", "workspace_lineages", "id", "NO ACTION"),
        ("source_checkpoint_id", "checkpoints", "id", "NO ACTION"),
        ("rollback_checkpoint_id", "checkpoints", "id", "NO ACTION"),
        ("replacement_task_id", "tasks", "id", "NO ACTION"),
    },
    "workspace_lineage_usage": {
        ("lineage_id", "workspace_lineages", "id", "CASCADE")
    },
    "workspace_mutations": {
        ("workspace_fingerprint", "workspace_rewind_coverage", "workspace_fingerprint", "NO ACTION"),
        ("owner_thread_id", "threads", "id", "NO ACTION"),
        ("origin_thread_id", "threads", "id", "NO ACTION"),
        ("task_id", "tasks", "id", "NO ACTION"),
    },
    "workspace_mutation_paths": {
        ("mutation_sequence", "workspace_mutations", "sequence", "CASCADE")
    },
    "checkpoint_rewind_facts": {
        ("checkpoint_id", "checkpoints", "id", "CASCADE"),
        ("owner_thread_id", "threads", "id", "NO ACTION"),
        ("workspace_fingerprint", "workspace_rewind_coverage", "workspace_fingerprint", "NO ACTION"),
    },
    "checkpoint_rewind_expectations": {
        ("checkpoint_id", "checkpoints", "id", "CASCADE")
    },
    "peer_sessions": {
        ("thread_id", "threads", "id", "SET NULL"),
        ("task_id", "tasks", "id", "SET NULL"),
    },
    "peer_messages": {
        ("sender_instance_id", "peer_sessions", "instance_id", "NO ACTION"),
        ("receiver_instance_id", "peer_sessions", "instance_id", "NO ACTION"),
    },
}

_INDEXES = {
    "workspace_lineages_status_updated": (
        "workspace_lineages", ("status", "updated_at", "id"), False, False
    ),
    "workspace_snapshots_lineage_created": (
        "workspace_snapshots", ("lineage_id", "created_at", "id"), False, False
    ),
    "rewind_operations_status_created": (
        "rewind_operations", ("status", "created_at", "id"), False, False
    ),
    "rewind_operations_lineage_status_created": (
        "rewind_operations", ("lineage_id", "status", "created_at", "id"), False, False
    ),
    "rewind_operations_one_pending": (
        "rewind_operations", ("lineage_id",), True, True
    ),
    "workspace_mutations_workspace_sequence": (
        "workspace_mutations", ("workspace_fingerprint", "sequence"), False, False
    ),
    "workspace_mutations_owner_sequence": (
        "workspace_mutations", ("owner_thread_id", "sequence"), False, False
    ),
    "workspace_mutation_paths_path_sequence": (
        "workspace_mutation_paths", ("path", "mutation_sequence"), False, False
    ),
    "checkpoint_rewind_facts_owner": (
        "checkpoint_rewind_facts", ("owner_thread_id", "mutation_sequence"), False, False
    ),
    "peer_sessions_live_name": (
        "peer_sessions", ("status", "heartbeat_at", "name", "session_ref"), False, False
    ),
    "peer_messages_receiver_status_created": (
        "peer_messages", ("receiver_instance_id", "status", "created_at", "id"), False, False
    ),
    "peer_messages_sender_created": (
        "peer_messages", ("sender_instance_id", "created_at", "id"), False, False
    ),
    "peer_messages_dedupe": (
        "peer_messages",
        ("sender_instance_id", "receiver_instance_id", "content_sha256", "created_at"),
        False,
        False,
    ),
}


def validate_schema_structure(connection: sqlite3.Connection) -> None:
    """Validate relationships and index semantics that column checks cannot see."""
    for table, expected in _FOREIGN_KEYS.items():
        if not expected.issubset(_foreign_keys(connection, table)):
            raise SessionCorruptionError(f"invalid foreign keys for {table}")
    for name, specification in _INDEXES.items():
        _validate_index(connection, name, *specification)
    _validate_worktree_uniqueness(connection)
    _validate_peer_ref_uniqueness(connection)


def _foreign_keys(
    connection: sqlite3.Connection, table: str
) -> set[tuple[str, str, str, str]]:
    rows = connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    return {(row[3], row[2], row[4], row[6].upper()) for row in rows}


def _validate_index(
    connection: sqlite3.Connection,
    name: str,
    table: str,
    columns: tuple[str, ...],
    unique: bool,
    partial: bool,
) -> None:
    row = _index_row(connection, table, name)
    if row is None or bool(row[2]) != unique or bool(row[4]) != partial:
        raise SessionCorruptionError(f"invalid required index {name}")
    keys = _index_keys(connection, name)
    if tuple(item[2] for item in keys) != columns:
        raise SessionCorruptionError(f"invalid required index {name}")
    if any(bool(item[3]) for item in keys):
        raise SessionCorruptionError(f"invalid required index {name}")
    if partial and _where_clause(connection, name) != "status = 'pending'":
        raise SessionCorruptionError(f"invalid required index {name}")


def _index_row(
    connection: sqlite3.Connection, table: str, name: str
) -> sqlite3.Row | tuple[object, ...] | None:
    rows = connection.execute(f'PRAGMA index_list("{table}")').fetchall()
    return next((row for row in rows if row[1] == name), None)


def _index_keys(
    connection: sqlite3.Connection, name: str
) -> list[sqlite3.Row | tuple[object, ...]]:
    rows = connection.execute(f'PRAGMA index_xinfo("{name}")').fetchall()
    return [row for row in rows if row[5]]


def _where_clause(connection: sqlite3.Connection, name: str) -> str | None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)
    ).fetchone()
    if row is None or not isinstance(row[0], str):
        return None
    match = re.search(r"\bwhere\b(.*)\Z", row[0], re.IGNORECASE | re.DOTALL)
    return " ".join(match.group(1).split()).lower() if match else None


def _validate_worktree_uniqueness(connection: sqlite3.Connection) -> None:
    rows = connection.execute('PRAGMA index_list("workspace_lineages")').fetchall()
    for row in rows:
        if not row[2]:
            continue
        keys = _index_keys(connection, row[1])
        if tuple(item[2] for item in keys) != ("worktree_root",):
            continue
        if len(keys) == 1 and str(keys[0][4]).upper() == "NOCASE":
            return
    raise SessionCorruptionError("invalid worktree_root uniqueness constraint")


def _validate_peer_ref_uniqueness(connection: sqlite3.Connection) -> None:
    row = _index_row(connection, "peer_sessions", "peer_sessions_ref_unique")
    if row is None or not bool(row[2]) or bool(row[4]):
        raise SessionCorruptionError("invalid peer session ref uniqueness constraint")
    keys = _index_keys(connection, "peer_sessions_ref_unique")
    if len(keys) != 1 or keys[0][2] != "session_ref":
        raise SessionCorruptionError("invalid peer session ref uniqueness constraint")
    if str(keys[0][4]).upper() != "NOCASE" or bool(keys[0][3]):
        raise SessionCorruptionError("invalid peer session ref uniqueness constraint")
