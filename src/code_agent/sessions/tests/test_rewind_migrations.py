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
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX messages_thread_sequence ON messages(thread_id, sequence);
            CREATE INDEX events_thread_sequence ON events(thread_id, sequence);
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
        timestamp = "2026-07-17T00:00:00Z"
        create_v10_database(self.database)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                ("legacy", timestamp, timestamp, None, "active"),
            )
            connection.execute(
                "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("checkpoint", "legacy", "old", "{}", timestamp, 0, 0),
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
                    "checkpoint_rewind_expectations",
                )
            )
            legacy = connection.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE id = 'checkpoint'"
            ).fetchone()[0]
        self.assertEqual(version, 11)
        self.assertEqual(counts, (0, 0, 0, 0, 0))
        self.assertEqual(legacy, 1)

    def test_v11_rejects_null_rewind_primary_keys(self) -> None:
        SQLiteSessionRepository(self.database)
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                "INSERT INTO threads(id,created_at,updated_at) "
                "VALUES ('legacy','now','now')"
            )
            connection.execute(
                "INSERT INTO workspace_rewind_coverage VALUES "
                "('workspace',1,'active',0,0,NULL,'now','now')"
            )
            null_primary_keys = (
                ("coverage", "INSERT INTO workspace_rewind_coverage VALUES "
                 "(NULL,1,'active',0,0,NULL,'now','now')"),
                ("checkpoint", "INSERT INTO checkpoint_rewind_facts VALUES "
                 "(NULL,'legacy','workspace',1,0,0,'active','now')"),
                ("expectation", "INSERT INTO checkpoint_rewind_expectations "
                 "VALUES (NULL,'now')"),
            )
            for name, statement in null_primary_keys:
                with self.subTest(null_primary_key=name), self.assertRaises(
                    sqlite3.IntegrityError):
                    connection.execute(statement)

    def test_v11_schema_corruption_fails_closed(self) -> None:
        plain_facts = (
            "CREATE TABLE checkpoint_rewind_facts (checkpoint_id TEXT, "
            "owner_thread_id TEXT, workspace_fingerprint TEXT, "
            "coverage_generation INTEGER, mutation_sequence INTEGER, "
            "mutation_count INTEGER, coverage_state TEXT, created_at TEXT)"
        )
        cases = (
            ("missing-table", lambda sql: sql if "checkpoint_rewind_facts" not in sql else ""),
            ("missing-parent", lambda sql: sql.replace("parent_request_id TEXT,", "")),
            ("missing-path-count", lambda sql: sql.replace(
                "path_count", "lost_path_count")),
            ("missing-mutation-count", lambda sql: sql.replace(
                "mutation_count", "lost_mutation_count")),
            ("missing-expectations", lambda sql: sql if
             "checkpoint_rewind_expectations" not in sql else ""),
            ("plain-table", lambda sql: plain_facts if sql.startswith(
                "CREATE TABLE checkpoint_rewind_facts") else sql),
            ("missing-index", lambda sql: sql if "workspace_mutations_owner_sequence"
             not in sql else ""),
        )
        for name, transform in cases:
            with self.subTest(corruption=name):
                database = Path(self.temporary.name) / f"{name}.sqlite3"
                create_v10_database(database)
                statements = tuple(filter(None, map(transform, _MIGRATIONS[11])))
                with sqlite3.connect(database) as connection:
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute("PRAGMA user_version = 11")
                with self.assertRaises(SessionCorruptionError):
                    SQLiteSessionRepository(database)

if __name__ == "__main__":
    unittest.main()
