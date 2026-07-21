from __future__ import annotations

import asyncio
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from .errors import (
    SessionCorruptionError,
    SessionError,
    SessionMigrationError,
    SessionStorageError,
)


SCHEMA_VERSION = 13
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
        "CREATE TABLE checkpoints (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, label TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL)",
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
}

_REQUIRED_COLUMNS = {
    "threads": {
        "id", "created_at", "updated_at", "title", "status", "parent_thread_id",
    },
    "messages": {"sequence", "thread_id", "payload", "created_at"},
    "events": {"sequence", "thread_id", "payload", "created_at"},
    "goals": {
        "id", "thread_id", "objective", "status", "metadata", "created_at",
        "updated_at",
    },
    "checkpoints": {"id", "thread_id", "label", "metadata", "created_at"},
    "task_budgets": {"thread_id", "model_name", "max_agent_rounds", "max_tool_calls", "max_tool_calls_per_round", "max_total_tokens", "model_turns", "tool_calls", "input_tokens", "output_tokens", "repair_cycles", "repeated_failures", "last_failure_signature", "active_seconds", "warned_at_80", "warned_at_90"},
    "task_states": {"thread_id", "payload", "updated_at"},
    "tasks": {"id", "thread_id", "contract", "status", "stop_reason", "created_at", "updated_at"},
    "task_controls": {"sequence", "task_id", "instruction", "created_at"},
    "task_executions": {"task_id", "instance_id", "owner_pid", "owner_create_time", "started_at"},
    "task_contract_revisions": {"task_id", "revision", "payload", "created_at"},
    "verification_runs": {"id", "task_id", "generation", "subject_hash", "status", "created_at", "completed_at"},
    "verification_evidence": {"id", "run_id", "task_id", "payload", "created_at"},
    "task_completions": {"task_id", "revision", "generation", "subject_hash", "assessment", "created_at"},
    "semantic_checkpoints": {"id", "thread_id", "payload", "created_at"},
    "thread_index_entries": {
        "stable_id", "checkpoint_id", "thread_id", "sequence", "text", "payload",
        "created_at",
    },
    "workflows": {
        "id", "root_thread_id", "task_id", "payload", "created_at", "updated_at",
    },
    "workflow_nodes": {"id", "workflow_id", "position", "status", "payload"},
    "workflow_edges": {
        "workflow_id", "source_node_id", "target_node_id", "kind", "position",
    },
    "skill_activations": {
        "thread_id", "skill_id", "source", "digest", "activated_at",
    },
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
        for table, expected in _REQUIRED_COLUMNS.items():
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
            columns = {row[1] for row in rows}
            if not expected.issubset(columns):
                raise SessionCorruptionError(f"session schema is missing {table}")

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
