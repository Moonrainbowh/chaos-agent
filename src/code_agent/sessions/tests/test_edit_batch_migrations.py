from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from code_agent.sessions._database import SCHEMA_VERSION, _MIGRATIONS
from code_agent.sessions.errors import SessionCorruptionError
from code_agent.sessions.repository import SQLiteSessionRepository


class EditBatchMigrationTests(unittest.TestCase):
    def test_v18_migrates_to_v19_with_empty_batch_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "sessions.sqlite3"
            with closing(sqlite3.connect(database)) as connection, connection:
                for version in range(1, 19):
                    for statement in _MIGRATIONS[version]:
                        connection.execute(statement)
                connection.execute("PRAGMA user_version = 18")
            SQLiteSessionRepository(database)
            with closing(sqlite3.connect(database)) as connection, connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                counts = tuple(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in (
                        "workspace_edit_batches",
                        "workspace_edit_batch_operations",
                    )
                )
                indexes = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA index_list(workspace_edit_batches)"
                    )
                }
                operation_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(workspace_edit_batch_operations)"
                    )
                }
            self.assertEqual(SCHEMA_VERSION, 19)
            self.assertEqual(version, 19)
            self.assertEqual(counts, (0, 0))
            self.assertIn("workspace_edit_batches_one_unresolved", indexes)
            self.assertTrue(
                {
                    "source_pre_size",
                    "source_post_size",
                    "target_pre_size",
                    "target_post_size",
                }.issubset(operation_columns)
            )

    def test_unresolved_index_requires_exact_partial_predicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "sessions.sqlite3"
            SQLiteSessionRepository(database)
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "DROP INDEX workspace_edit_batches_one_unresolved"
                )
                connection.execute(
                    "CREATE UNIQUE INDEX workspace_edit_batches_one_unresolved "
                    "ON workspace_edit_batches(workspace_fingerprint) "
                    "WHERE state = 'prepared'"
                )
            with self.assertRaises(SessionCorruptionError):
                SQLiteSessionRepository(database)

    def test_operation_size_checks_reject_inconsistent_rows(self) -> None:
        statement = (
            "INSERT INTO workspace_edit_batch_operations("
            "mutation_sequence, ordinal, kind, source_path, "
            "source_pre_existed, source_pre_sha256, source_pre_size, "
            "source_post_existed, source_post_sha256, source_post_size, "
            "target_path, target_pre_existed, target_pre_sha256, "
            "target_pre_size, target_post_existed, target_post_sha256, "
            "target_post_size, case_only, progress, committed_at"
            ") VALUES (1, 0, 'move', 'a.txt', 1, ?, 7, 0, NULL, 0, "
            "'b.txt', 0, NULL, ?, 1, ?, ?, 0, 'pending', NULL)"
        )
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "sessions.sqlite3"
            SQLiteSessionRepository(database)
            with closing(sqlite3.connect(database)) as connection, connection:
                for target_pre_size, target_post_size in ((1, 7), (0, 8)):
                    with self.subTest(
                        target_pre_size=target_pre_size,
                        target_post_size=target_post_size,
                    ), self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            statement,
                            (
                                "a" * 64,
                                target_pre_size,
                                "a" * 64,
                                target_post_size,
                            ),
                        )


if __name__ == "__main__":
    unittest.main()
