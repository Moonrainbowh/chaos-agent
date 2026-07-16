from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import Message  # noqa: E402
from code_agent.sessions._database import SCHEMA_VERSION  # noqa: E402
from code_agent.sessions.errors import (  # noqa: E402
    SessionCorruptionError,
    SessionMigrationError,
)
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.sessions.legacy_migration import migrate_legacy_session_database  # noqa: E402


def create_v1_database(path: Path) -> None:
    connection = sqlite3.connect(path)
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
        PRAGMA user_version = 1;
        """
    )
    connection.commit()
    connection.close()


def create_v3_database(path: Path) -> None:
    create_v1_database(path)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            ALTER TABLE threads ADD COLUMN title TEXT;
            ALTER TABLE threads ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
            CREATE TABLE goals (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, objective TEXT NOT NULL, status TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE checkpoints (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, label TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX goals_thread_created ON goals(thread_id, created_at, id);
            CREATE INDEX checkpoints_thread_created ON checkpoints(thread_id, created_at, id);
            CREATE TABLE task_budgets (thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE, model_name TEXT NOT NULL, max_agent_rounds INTEGER NOT NULL, max_tool_calls INTEGER NOT NULL, max_tool_calls_per_round INTEGER NOT NULL, model_turns INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0);
            PRAGMA user_version = 3;
            """
        )


class SessionMigrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_v1_database_migrates_without_losing_records(self) -> None:
        create_v1_database(self.database)
        timestamp = "2026-07-11T00:00:00Z"
        message = Message("user", "legacy")
        event = AgentEvent(
            EventKind.TURN_STARTED,
            {"turn": 1},
            datetime(2026, 7, 11, tzinfo=timezone.utc),
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?)",
                ("thread-v1", timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO messages(thread_id, payload, created_at) VALUES (?, ?, ?)",
                ("thread-v1", json.dumps(message.to_dict()), timestamp),
            )
            connection.execute(
                "INSERT INTO events(thread_id, payload, created_at) VALUES (?, ?, ?)",
                ("thread-v1", json.dumps(event.to_dict()), timestamp),
            )

        repository = SQLiteSessionRepository(self.database)

        self.assertEqual(tuple(await repository.load_messages("thread-v1")), (message,))
        self.assertEqual(await repository.load_events("thread-v1"), (event,))
        await repository.create_goal("thread-v1", "migrated goal")
        with sqlite3.connect(self.database) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, SCHEMA_VERSION)

    def test_v3_database_migrates_task_state_table(self) -> None:
        create_v3_database(self.database)

        SQLiteSessionRepository(self.database)

        with sqlite3.connect(self.database) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertIn("task_states", tables)

    def test_future_schema_version_is_rejected_without_mutation(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA user_version = 999")

        with self.assertRaises(SessionMigrationError):
            SQLiteSessionRepository(self.database)

        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 999)

    def test_random_bytes_are_rejected_without_replacement(self) -> None:
        damaged = b"not a sqlite database\x00keep-me"
        self.database.write_bytes(damaged)

        with self.assertRaises(SessionCorruptionError):
            SQLiteSessionRepository(self.database)

        self.assertEqual(self.database.read_bytes(), damaged)

    def test_current_version_with_missing_tables_is_rejected(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        with self.assertRaises(SessionCorruptionError):
            SQLiteSessionRepository(self.database)

    async def test_malformed_record_json_fails_closed(self) -> None:
        repository = SQLiteSessionRepository(self.database)
        thread_id = await repository.create_thread()
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO messages(thread_id, payload, created_at) VALUES (?, ?, ?)",
                (thread_id, "{broken", "2026-07-11T00:00:00Z"),
            )

        with self.assertRaises(SessionCorruptionError):
            await repository.load_messages(thread_id)

    async def test_malformed_task_state_json_fails_closed(self) -> None:
        repository = SQLiteSessionRepository(self.database)
        thread_id = await repository.create_thread()
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO task_states(thread_id, payload, updated_at) VALUES (?, ?, ?)",
                (thread_id, "{broken", "2026-07-11T00:00:00Z"),
            )

        with self.assertRaises(SessionCorruptionError):
            await repository.load_task_state(thread_id)

    def test_failed_migration_rolls_back_schema_and_version(self) -> None:
        create_v1_database(self.database)
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE VIEW goals AS SELECT 1 AS value")

        with self.assertRaises(SessionMigrationError):
            SQLiteSessionRepository(self.database)

        with sqlite3.connect(self.database) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(threads)")
            }
        self.assertEqual(version, 1)
        self.assertNotIn("title", columns)

    def test_legacy_database_copy_is_atomic_and_idempotent(self) -> None:
        create_v1_database(self.database)
        with sqlite3.connect(self.database) as connection:
            connection.execute("INSERT INTO threads VALUES ('legacy', '2026-01-01Z', '2026-01-01Z')")
        target = Path(self.temporary.name) / "new" / "sessions.sqlite3"

        first = migrate_legacy_session_database(self.database, target)
        second = migrate_legacy_session_database(self.database, target)

        self.assertTrue(first.migrated)
        self.assertEqual(first.thread_count, 1)
        self.assertFalse(second.migrated)
        self.assertTrue(self.database.exists())
        self.assertFalse(target.with_suffix(".sqlite3.migrating").exists())


if __name__ == "__main__":
    unittest.main()
