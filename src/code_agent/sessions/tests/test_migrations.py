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


if __name__ == "__main__":
    unittest.main()
