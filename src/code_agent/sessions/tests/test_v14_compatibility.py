from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.sessions._database import SCHEMA_VERSION, _MIGRATIONS  # noqa: E402
from code_agent.sessions.errors import SessionCorruptionError  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.workspace._snapshot_manifest import manifest_digest  # noqa: E402


LEGACY_MIGRATION_14 = (
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
)

STAMP = "2026-07-22T00:00:00+00:00"


def create_legacy_v14(path: Path) -> dict[str, str]:
    identifiers = _identifiers()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for version in range(1, 14):
            for statement in _MIGRATIONS[version]:
                connection.execute(statement)
        for statement in LEGACY_MIGRATION_14:
            connection.execute(statement)
        _insert_legacy_facts(connection, identifiers)
        connection.execute("PRAGMA user_version = 14")
    return identifiers


def _identifiers() -> dict[str, str]:
    names = (
        "available_thread", "inferred_thread", "legacy_thread", "available_task",
        "inferred_task", "available_lineage", "inferred_lineage", "snapshot",
        "available_checkpoint", "inferred_checkpoint", "legacy_checkpoint",
        "no_owner_lineage", "unbound_thread", "unbound_task", "unbound_lineage",
    )
    return {name: format(index + 1, "032x") for index, name in enumerate(names)}


def _insert_legacy_facts(connection: sqlite3.Connection, ids: dict[str, str]) -> None:
    for thread in ("available_thread", "inferred_thread", "legacy_thread"):
        connection.execute(
            "INSERT INTO threads(id, created_at, updated_at, status) VALUES (?, ?, ?, 'active')",
            (ids[thread], STAMP, STAMP),
        )
    for task, thread in (("available_task", "available_thread"), ("inferred_task", "inferred_thread")):
        connection.execute(
            "INSERT INTO tasks(id, thread_id, contract, status, created_at, updated_at) "
            "VALUES (?, ?, '{}', 'created', ?, ?)",
            (ids[task], ids[thread], STAMP, STAMP),
        )
    _insert_lineages(connection, ids)
    _insert_budgets(connection, ids)
    _insert_checkpoints(connection, ids)


def _insert_lineages(connection: sqlite3.Connection, ids: dict[str, str]) -> None:
    for prefix in ("available", "inferred"):
        connection.execute(
            "INSERT INTO workspace_lineages VALUES (?, 'repo', 'C:/source', ?, ?, ?, ?, 'active', ?, ?)",
            (ids[f"{prefix}_lineage"], f"C:/work/{prefix}", f"codex/{prefix}", "a" * 40, ids[f"{prefix}_task"], STAMP, STAMP),
        )
        connection.execute(
            "UPDATE tasks SET workspace_lineage_id = ? WHERE id = ?",
            (ids[f"{prefix}_lineage"], ids[f"{prefix}_task"]),
        )


def _insert_budgets(connection: sqlite3.Connection, ids: dict[str, str]) -> None:
    connection.execute(
        "INSERT INTO task_budgets(thread_id, model_name, max_agent_rounds, max_tool_calls, "
        "max_tool_calls_per_round, repeated_failures, last_failure_signature) "
        "VALUES (?, 'model', 20, 20, 20, 3, 'failure-a')",
        (ids["available_thread"],),
    )
    connection.execute(
        "INSERT INTO workspace_lineage_usage(lineage_id, repeated_failures) VALUES (?, 99)",
        (ids["available_lineage"],),
    )
    connection.execute(
        "INSERT INTO workspace_lineage_usage(lineage_id) VALUES (?)",
        (ids["inferred_lineage"],),
    )


def _insert_checkpoints(connection: sqlite3.Connection, ids: dict[str, str]) -> None:
    connection.execute(
        "INSERT INTO workspace_snapshots VALUES (?, ?, ?, 0, ?)",
        (ids["snapshot"], ids["available_lineage"], manifest_digest(()), STAMP),
    )
    facts = (
        ("available", ids["snapshot"], "available"),
        ("inferred", None, "unavailable"),
        ("legacy", None, "unavailable"),
    )
    for prefix, snapshot_id, status in facts:
        checkpoint = ids[f"{prefix}_checkpoint"]
        connection.execute(
            "INSERT INTO checkpoints(id, thread_id, label, metadata, created_at) "
            "VALUES (?, ?, ?, '{}', ?)",
            (checkpoint, ids[f"{prefix}_thread"], prefix, STAMP),
        )
        connection.execute(
            "INSERT INTO checkpoint_workspace_state VALUES (?, ?, 0, 0, '[]', '{}', '{}', ?)",
            (checkpoint, snapshot_id, status),
        )


class V14CompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.database = Path(self.temporary.name) / "sessions.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_real_v14_upgrade_backfills_rewindable_facts_and_keeps_legacy(self) -> None:
        ids = create_legacy_v14(self.database)
        self.assertEqual(_MIGRATIONS[14], LEGACY_MIGRATION_14)
        repository = SQLiteSessionRepository(self.database)
        SQLiteSessionRepository(self.database)

        available = await repository.load_checkpoint_cursor(ids["available_checkpoint"])
        inferred = await repository.load_checkpoint_cursor(ids["inferred_checkpoint"])
        self.assertEqual(available.lineage_id, ids["available_lineage"])
        self.assertEqual(inferred.lineage_id, ids["inferred_lineage"])
        self.assertEqual(
            (await repository.load_workspace_snapshot(ids["available_checkpoint"])).id,
            ids["snapshot"],
        )
        self.assertIsNone(
            await repository.load_workspace_snapshot(ids["inferred_checkpoint"])
        )
        with self.assertRaisesRegex(SessionCorruptionError, "not rewindable"):
            await repository.load_checkpoint_cursor(ids["legacy_checkpoint"])
        with self.assertRaisesRegex(SessionCorruptionError, "not rewindable"):
            await repository.load_workspace_snapshot(ids["legacy_checkpoint"])
        with self.assertRaisesRegex(SessionCorruptionError, "not rewindable"):
            await repository.fork_task_from_checkpoint(ids["legacy_checkpoint"])
        self.assertEqual(len(await repository.list_checkpoints(ids["legacy_thread"])), 1)
        self.assertEqual(
            _migration_facts(self.database, ids),
            (SCHEMA_VERSION, 3, "failure-a", None),
        )

    def test_failure_pair_backfill_uses_owner_pair_or_conservative_zero(self) -> None:
        ids = create_legacy_v14(self.database)
        SQLiteSessionRepository(self.database)
        _prepare_failure_pair_cases(self.database, ids)
        with sqlite3.connect(self.database) as connection:
            connection.execute(_MIGRATIONS[15][-1])
            connection.execute(_MIGRATIONS[15][-1])

        self.assertEqual(
            _failure_pairs(self.database, ids),
            ((0, None, 11), (0, None, 12), (0, None, 13), (0, None, 14)),
        )


def _migration_facts(
    path: Path, ids: dict[str, str]
) -> tuple[int, int, str | None, str | None]:
    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        usage = connection.execute(
            "SELECT repeated_failures, last_failure_signature "
            "FROM workspace_lineage_usage WHERE lineage_id = ?",
            (ids["available_lineage"],),
        ).fetchone()
        missing_signature = connection.execute(
            "SELECT last_failure_signature FROM workspace_lineage_usage "
            "WHERE lineage_id = ?",
            (ids["inferred_lineage"],),
        ).fetchone()[0]
    return version, usage[0], usage[1], missing_signature


def _prepare_failure_pair_cases(path: Path, ids: dict[str, str]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE task_budgets SET repeated_failures = 0, last_failure_signature = NULL "
            "WHERE thread_id = ?",
            (ids["available_thread"],),
        )
        _set_old_pair(connection, ids["available_lineage"], 11)
        _set_old_pair(connection, ids["inferred_lineage"], 14)
        _insert_untrusted_lineage(connection, ids, "no_owner", 12)
        _insert_untrusted_lineage(connection, ids, "unbound", 13)


def _set_old_pair(connection: sqlite3.Connection, lineage_id: str, turns: int) -> None:
    connection.execute(
        "UPDATE workspace_lineage_usage SET repeated_failures = 99, "
        "last_failure_signature = 'old', model_turns = ? WHERE lineage_id = ?",
        (turns, lineage_id),
    )


def _insert_untrusted_lineage(
    connection: sqlite3.Connection, ids: dict[str, str], kind: str, turns: int
) -> None:
    owner = None
    if kind == "unbound":
        connection.execute(
            "INSERT INTO threads(id, created_at, updated_at, status) VALUES (?, ?, ?, 'active')",
            (ids["unbound_thread"], STAMP, STAMP),
        )
        connection.execute(
            "INSERT INTO tasks(id, thread_id, contract, status, created_at, updated_at) "
            "VALUES (?, ?, '{}', 'created', ?, ?)",
            (ids["unbound_task"], ids["unbound_thread"], STAMP, STAMP),
        )
        owner = ids["unbound_task"]
    lineage = ids[f"{kind}_lineage"]
    connection.execute(
        "INSERT INTO workspace_lineages VALUES (?, 'repo', 'C:/source', ?, ?, ?, ?, 'active', ?, ?)",
        (lineage, f"C:/work/{kind}", f"codex/{kind}", "a" * 40, owner, STAMP, STAMP),
    )
    connection.execute(
        "INSERT INTO workspace_lineage_usage(lineage_id, repeated_failures, "
        "last_failure_signature, model_turns) VALUES (?, 99, 'old', ?)",
        (lineage, turns),
    )


def _failure_pairs(
    path: Path, ids: dict[str, str]
) -> tuple[tuple[int, str | None, int], ...]:
    order = (
        "available_lineage", "no_owner_lineage", "unbound_lineage", "inferred_lineage"
    )
    with sqlite3.connect(path) as connection:
        return tuple(
            connection.execute(
                "SELECT repeated_failures, last_failure_signature, model_turns "
                "FROM workspace_lineage_usage WHERE lineage_id = ?",
                (ids[name],),
            ).fetchone()
            for name in order
        )


if __name__ == "__main__":
    unittest.main()
