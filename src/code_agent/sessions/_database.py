from __future__ import annotations

import asyncio
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from ._rewind_schema import REWIND_MIGRATION, REWIND_REQUIRED_COLUMNS
from .errors import (
    SessionCorruptionError,
    SessionError,
    SessionMigrationError,
    SessionStorageError,
)
from ._schema_structure import validate_schema_structure
from ._schema_validation import REQUIRED_COLUMNS


SCHEMA_VERSION = 17
_BUSY_TIMEOUT_MS = 5_000
_SQLITE_CORRUPT = 11
_SQLITE_NOTADB = 26
_Result = TypeVar("_Result")

_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        "CREATE TABLE threads (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        "CREATE TABLE messages (sequence INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE INDEX messages_thread_sequence ON messages(thread_id, sequence)",
        "CREATE INDEX events_thread_sequence ON events(thread_id, sequence)",
    ),
    2: (
        "ALTER TABLE threads ADD COLUMN title TEXT",
        "ALTER TABLE threads ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
        "CREATE TABLE goals (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, objective TEXT NOT NULL, status TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        "CREATE TABLE checkpoints (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, label TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL, message_sequence INTEGER, event_sequence INTEGER)",
        "CREATE INDEX goals_thread_created ON goals(thread_id, created_at, id)",
        "CREATE INDEX checkpoints_thread_created ON checkpoints(thread_id, created_at, id)",
    ),
    3: (
        "CREATE TABLE task_budgets (thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE, model_name TEXT NOT NULL, max_agent_rounds INTEGER NOT NULL, max_tool_calls INTEGER NOT NULL, max_tool_calls_per_round INTEGER NOT NULL, model_turns INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0)",
    ),
    4: (
        "CREATE TABLE task_states (thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE, payload TEXT NOT NULL, updated_at TEXT NOT NULL)",
    ),
    5: (
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL UNIQUE REFERENCES threads(id) ON DELETE CASCADE, contract TEXT NOT NULL, status TEXT NOT NULL, stop_reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        "CREATE INDEX tasks_status_updated ON tasks(status, updated_at DESC, id)",
        "ALTER TABLE task_budgets ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE task_budgets ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE task_budgets ADD COLUMN repair_cycles INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE task_budgets ADD COLUMN repeated_failures INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE task_budgets ADD COLUMN last_failure_signature TEXT",
    ),
    6: (
        "ALTER TABLE task_budgets ADD COLUMN active_seconds INTEGER NOT NULL DEFAULT 0",
        "CREATE TABLE task_controls (sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, instruction TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE INDEX task_controls_task_sequence ON task_controls(task_id, sequence)",
    ),
    7: (
        "CREATE TABLE task_executions (task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE, instance_id TEXT NOT NULL, owner_pid INTEGER NOT NULL, owner_create_time REAL NOT NULL, started_at TEXT NOT NULL)",
        "CREATE INDEX task_executions_owner ON task_executions(owner_pid, owner_create_time)",
    ),
    8: (
        "ALTER TABLE task_budgets ADD COLUMN max_total_tokens INTEGER NOT NULL DEFAULT 200000",
        "ALTER TABLE task_budgets ADD COLUMN warned_at_80 INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE task_budgets ADD COLUMN warned_at_90 INTEGER NOT NULL DEFAULT 0",
    ),
    9: (
        "CREATE TABLE task_contract_revisions (task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, revision INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(task_id, revision))",
        "CREATE TABLE verification_runs (id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, generation INTEGER NOT NULL, subject_hash TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT)",
        "CREATE TABLE verification_evidence (id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES verification_runs(id) ON DELETE CASCADE, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE task_completions (task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE, revision INTEGER NOT NULL, generation INTEGER NOT NULL, subject_hash TEXT NOT NULL, assessment TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE INDEX verification_runs_task_created ON verification_runs(task_id, created_at)",
        "CREATE INDEX verification_evidence_task_created ON verification_evidence(task_id, created_at)",
    ),
    10: (
        "ALTER TABLE threads ADD COLUMN parent_thread_id TEXT REFERENCES threads(id)",
        "CREATE INDEX threads_parent_id ON threads(parent_thread_id)",
    ),
    11: (
        "CREATE TABLE semantic_checkpoints (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE thread_index_entries (stable_id TEXT PRIMARY KEY, checkpoint_id TEXT NOT NULL REFERENCES semantic_checkpoints(id) ON DELETE CASCADE, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, sequence INTEGER NOT NULL, text TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE INDEX semantic_checkpoints_thread_created ON semantic_checkpoints(thread_id, created_at, id)",
        "CREATE INDEX thread_index_thread_sequence ON thread_index_entries(thread_id, sequence, stable_id)",
    ),
    12: (
        "CREATE TABLE workflows (id TEXT PRIMARY KEY, root_thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, task_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        "CREATE TABLE workflow_nodes (id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE, position INTEGER NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)",
        "CREATE TABLE workflow_edges (workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE, source_node_id TEXT NOT NULL REFERENCES workflow_nodes(id) ON DELETE CASCADE, target_node_id TEXT NOT NULL REFERENCES workflow_nodes(id) ON DELETE CASCADE, kind TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(workflow_id, source_node_id, target_node_id, kind))",
        "CREATE INDEX workflow_nodes_workflow_position ON workflow_nodes(workflow_id, position)",
        "CREATE INDEX workflow_edges_workflow_position ON workflow_edges(workflow_id, position)",
    ),
    13: (
        "CREATE TABLE skill_activations (thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, skill_id TEXT NOT NULL, source TEXT NOT NULL, digest TEXT NOT NULL, activated_at TEXT NOT NULL, PRIMARY KEY(thread_id, skill_id))",
        "CREATE INDEX skill_activations_thread_time ON skill_activations(thread_id, activated_at, skill_id)",
    ),
    14: (
        "CREATE TABLE workspace_lineages (id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, source_root TEXT NOT NULL, worktree_root TEXT NOT NULL UNIQUE COLLATE NOCASE, branch_name TEXT NOT NULL, head_commit TEXT NOT NULL, owner_task_id TEXT REFERENCES tasks(id), status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        "ALTER TABLE tasks ADD COLUMN workspace_lineage_id TEXT REFERENCES workspace_lineages(id)",
        "CREATE TABLE workspace_snapshots (id TEXT PRIMARY KEY, lineage_id TEXT NOT NULL REFERENCES workspace_lineages(id), inventory_digest TEXT NOT NULL, total_bytes INTEGER NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE workspace_snapshot_entries (snapshot_id TEXT NOT NULL REFERENCES workspace_snapshots(id) ON DELETE CASCADE, relative_path TEXT NOT NULL, existed INTEGER NOT NULL, blob_sha256 TEXT, size INTEGER NOT NULL, mode INTEGER, PRIMARY KEY(snapshot_id, relative_path))",
        "CREATE TABLE checkpoint_workspace_state (checkpoint_id TEXT PRIMARY KEY REFERENCES checkpoints(id) ON DELETE CASCADE, snapshot_id TEXT REFERENCES workspace_snapshots(id), message_sequence INTEGER NOT NULL, event_sequence INTEGER NOT NULL, goals_payload TEXT NOT NULL, task_state_payload TEXT NOT NULL, budget_payload TEXT NOT NULL, snapshot_status TEXT NOT NULL)",
        "CREATE TABLE rewind_operations (id TEXT PRIMARY KEY, lineage_id TEXT NOT NULL REFERENCES workspace_lineages(id), source_checkpoint_id TEXT NOT NULL REFERENCES checkpoints(id), rollback_checkpoint_id TEXT REFERENCES checkpoints(id), mode TEXT NOT NULL, preview_fingerprint TEXT NOT NULL, status TEXT NOT NULL, error_code TEXT, replacement_task_id TEXT REFERENCES tasks(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        "CREATE TABLE workspace_lineage_usage (lineage_id TEXT PRIMARY KEY REFERENCES workspace_lineages(id) ON DELETE CASCADE, model_turns INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, repair_cycles INTEGER NOT NULL DEFAULT 0, repeated_failures INTEGER NOT NULL DEFAULT 0, active_seconds INTEGER NOT NULL DEFAULT 0, warned_at_80 INTEGER NOT NULL DEFAULT 0, warned_at_90 INTEGER NOT NULL DEFAULT 0)",
        "CREATE INDEX workspace_lineages_status_updated ON workspace_lineages(status, updated_at, id)",
        "CREATE INDEX workspace_snapshots_lineage_created ON workspace_snapshots(lineage_id, created_at, id)",
        "CREATE INDEX rewind_operations_status_created ON rewind_operations(status, created_at, id)",
        "CREATE INDEX rewind_operations_lineage_status_created ON rewind_operations(lineage_id, status, created_at, id)",
        "CREATE UNIQUE INDEX rewind_operations_one_pending ON rewind_operations(lineage_id) WHERE status = 'pending'",
    ),
    15: (
        "ALTER TABLE checkpoint_workspace_state ADD COLUMN lineage_id TEXT REFERENCES workspace_lineages(id)",
        "ALTER TABLE workspace_lineage_usage ADD COLUMN last_failure_signature TEXT",
        "UPDATE checkpoint_workspace_state SET lineage_id = (SELECT lineage_id FROM workspace_snapshots WHERE id = checkpoint_workspace_state.snapshot_id) WHERE snapshot_id IS NOT NULL",
        "UPDATE checkpoint_workspace_state SET lineage_id = (SELECT t.workspace_lineage_id FROM checkpoints c JOIN tasks t ON t.thread_id = c.thread_id WHERE c.id = checkpoint_workspace_state.checkpoint_id AND t.workspace_lineage_id IS NOT NULL) WHERE lineage_id IS NULL AND snapshot_id IS NULL",
        "UPDATE workspace_lineage_usage SET repeated_failures = COALESCE((SELECT b.repeated_failures FROM workspace_lineages l JOIN tasks t ON t.id = l.owner_task_id AND t.workspace_lineage_id = l.id JOIN task_budgets b ON b.thread_id = t.thread_id WHERE l.id = workspace_lineage_usage.lineage_id), 0), last_failure_signature = (SELECT b.last_failure_signature FROM workspace_lineages l JOIN tasks t ON t.id = l.owner_task_id AND t.workspace_lineage_id = l.id JOIN task_budgets b ON b.thread_id = t.thread_id WHERE l.id = workspace_lineage_usage.lineage_id)",
    ),
    16: REWIND_MIGRATION,
    17: (),
}


class SessionDatabase:
    """Open short-lived SQLite connections around bounded transactions."""

    def __init__(self, path: os.PathLike[str] | str) -> None:
        raw_path = os.fspath(path)
        if not isinstance(raw_path, str):
            raise TypeError("database path must be text")
        self.path = Path(raw_path).expanduser().resolve(strict=False)
        if self.path.exists() and self.path.is_dir():
            raise ValueError("database path must not be a directory")
        if not self.path.parent.exists() or not self.path.parent.is_dir():
            raise ValueError("database parent must be an existing directory")
        self._initialize()

    async def read(
        self, operation: Callable[[sqlite3.Connection], _Result]
    ) -> _Result:
        return await asyncio.to_thread(self._execute, operation, False)

    async def write(
        self, operation: Callable[[sqlite3.Connection], _Result]
    ) -> _Result:
        return await asyncio.to_thread(self._execute, operation, True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=_BUSY_TIMEOUT_MS / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        return connection

    def _initialize(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            self._quick_check(connection)
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise SessionMigrationError(
                    f"database schema {version} is newer than {SCHEMA_VERSION}"
                )
            if version < SCHEMA_VERSION:
                self._migrate(connection, version)
            self._quick_check(connection)
            self._validate_schema(connection)
        except SessionError:
            raise
        except sqlite3.DatabaseError as error:
            raise _database_error(error) from error
        finally:
            if connection is not None:
                connection.close()

    def _migrate(self, connection: sqlite3.Connection, version: int) -> None:
        try:
            connection.execute("BEGIN EXCLUSIVE")
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            for target in range(current + 1, SCHEMA_VERSION + 1):
                for statement in _MIGRATIONS[target]:
                    connection.execute(statement)
                if target == 17:
                    _add_checkpoint_sequence_columns(connection)
                connection.execute(f"PRAGMA user_version = {target}")
            connection.execute("COMMIT")
        except sqlite3.DatabaseError as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise SessionMigrationError(
                f"failed to migrate schema from version {version}"
            ) from error

    @staticmethod
    def _quick_check(connection: sqlite3.Connection) -> None:
        rows = connection.execute("PRAGMA quick_check").fetchall()
        if not rows or any(row[0] != "ok" for row in rows):
            raise SessionCorruptionError("SQLite integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SessionCorruptionError("SQLite foreign-key check failed")

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        for table, expected in REQUIRED_COLUMNS.items():
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
            columns = {row[1] for row in rows}
            if not expected.issubset(columns):
                raise SessionCorruptionError(f"session schema is missing {table}")
        for table, expected in REWIND_REQUIRED_COLUMNS.items():
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
            columns = {row[1] for row in rows}
            if not expected.issubset(columns):
                raise SessionCorruptionError(f"session schema is missing {table}")
        validate_schema_structure(connection)

    def _execute(
        self,
        operation: Callable[[sqlite3.Connection], _Result],
        write: bool,
    ) -> _Result:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            result = operation(connection)
            connection.execute("COMMIT")
            return result
        except SessionError:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except sqlite3.DatabaseError as error:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise _database_error(error) from error
        finally:
            if connection is not None:
                connection.close()


def _database_error(error: sqlite3.DatabaseError) -> SessionError:
    code = getattr(error, "sqlite_errorcode", -1) & 0xFF
    legacy_corruption_messages = {
        "database disk image is malformed",
        "file is not a database",
    }
    if code in {_SQLITE_CORRUPT, _SQLITE_NOTADB} or (
        code == 255 and str(error).lower() in legacy_corruption_messages
    ):
        return SessionCorruptionError("SQLite database is corrupt")
    return SessionStorageError("SQLite session operation failed")


def _add_checkpoint_sequence_columns(connection: sqlite3.Connection) -> None:
    """Repair legacy v2/v3 checkpoint tables that omitted cursor columns."""
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(checkpoints)")
    }
    if "message_sequence" not in columns:
        connection.execute("ALTER TABLE checkpoints ADD COLUMN message_sequence INTEGER")
    if "event_sequence" not in columns:
        connection.execute("ALTER TABLE checkpoints ADD COLUMN event_sequence INTEGER")
