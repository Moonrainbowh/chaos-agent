from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.sessions.errors import SessionCorruptionError  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402


class SchemaStructureValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        SQLiteSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def rewrite_table_sql(self, table: str, old: str, new: str) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()[0]
            self.assertIn(old, sql)
            connection.execute("PRAGMA writable_schema = ON")
            connection.execute(
                "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?",
                (sql.replace(old, new), table),
            )
            version = connection.execute("PRAGMA schema_version").fetchone()[0]
            connection.execute(f"PRAGMA schema_version = {version + 1}")
            connection.execute("PRAGMA writable_schema = OFF")

    def assert_reopen_corrupt(self) -> None:
        with self.assertRaises(SessionCorruptionError):
            SQLiteSessionRepository(self.database)

    def test_same_name_index_with_wrong_columns_is_rejected(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DROP INDEX rewind_operations_status_created")
            connection.execute(
                "CREATE INDEX rewind_operations_status_created "
                "ON rewind_operations(lineage_id, id)"
            )
        self.assert_reopen_corrupt()

    def test_pending_index_must_be_unique_and_have_exact_partial_predicate(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DROP INDEX rewind_operations_one_pending")
            connection.execute(
                "CREATE UNIQUE INDEX rewind_operations_one_pending "
                "ON rewind_operations(lineage_id) WHERE status = 'completed'"
            )
        self.assert_reopen_corrupt()

    def test_missing_fk_and_wrong_on_delete_are_rejected(self) -> None:
        self.rewrite_table_sql(
            "checkpoint_workspace_state",
            "lineage_id TEXT REFERENCES workspace_lineages(id)",
            "lineage_id TEXT",
        )
        self.assert_reopen_corrupt()

        self.database.unlink()
        SQLiteSessionRepository(self.database)
        self.rewrite_table_sql(
            "workspace_snapshot_entries",
            "REFERENCES workspace_snapshots(id) ON DELETE CASCADE",
            "REFERENCES workspace_snapshots(id)",
        )
        self.assert_reopen_corrupt()

    def test_worktree_unique_index_must_really_use_nocase(self) -> None:
        self.rewrite_table_sql(
            "workspace_lineages",
            "worktree_root TEXT NOT NULL UNIQUE COLLATE NOCASE",
            "worktree_root TEXT NOT NULL UNIQUE",
        )
        self.assert_reopen_corrupt()

    def test_peer_ref_unique_index_must_really_use_nocase(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DROP INDEX peer_sessions_ref_unique")
            connection.execute(
                "CREATE UNIQUE INDEX peer_sessions_ref_unique "
                "ON peer_sessions(session_ref)"
            )
        self.assert_reopen_corrupt()


if __name__ == "__main__":
    unittest.main()
