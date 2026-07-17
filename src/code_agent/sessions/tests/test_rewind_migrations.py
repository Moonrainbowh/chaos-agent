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


def create_v10_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX messages_thread_sequence
                ON messages(thread_id, sequence);
            CREATE INDEX events_thread_sequence
                ON events(thread_id, sequence);
            PRAGMA user_version = 1;
            """
        )
        for version in range(2, 11):
            for statement in _MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {version}")


class RewindMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_v10_migrates_to_v11_with_only_empty_rewind_tables(self) -> None:
        create_v10_database(self.database)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                (
                    "legacy",
                    "2026-07-17T00:00:00Z",
                    "2026-07-17T00:00:00Z",
                    None,
                    "active",
                ),
            )
            connection.execute(
                "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "checkpoint",
                    "legacy",
                    "old",
                    "{}",
                    "2026-07-17T00:00:00Z",
                    0,
                    0,
                ),
            )

        SQLiteSessionRepository(self.database)

        with sqlite3.connect(self.database) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            counts = tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "workspace_rewind_coverage",
                    "workspace_mutations",
                    "workspace_mutation_paths",
                    "checkpoint_rewind_facts",
                )
            )
            legacy = connection.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE id = 'checkpoint'"
            ).fetchone()[0]
        self.assertEqual(version, 11)
        self.assertEqual(counts, (0, 0, 0, 0))
        self.assertEqual(legacy, 1)

    def test_v11_missing_rewind_table_fails_schema_check(self) -> None:
        create_v10_database(self.database)
        with sqlite3.connect(self.database) as connection:
            for statement in _MIGRATIONS[11]:
                connection.execute(statement)
            connection.execute("DROP TABLE checkpoint_rewind_facts")
            connection.execute("PRAGMA user_version = 11")

        with self.assertRaises(SessionCorruptionError):
            SQLiteSessionRepository(self.database)

    def test_v11_missing_parent_request_column_fails_schema_check(self) -> None:
        create_v10_database(self.database)
        statements = tuple(
            statement.replace("parent_request_id TEXT,", "")
            for statement in _MIGRATIONS[11]
        )
        with sqlite3.connect(self.database) as connection:
            for statement in statements:
                connection.execute(statement)
            connection.execute("PRAGMA user_version = 11")

        with self.assertRaises(SessionCorruptionError):
            SQLiteSessionRepository(self.database)


if __name__ == "__main__":
    unittest.main()
