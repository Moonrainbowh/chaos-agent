from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import Message  # noqa: E402
from code_agent.sessions._database import SCHEMA_VERSION, _MIGRATIONS  # noqa: E402
from code_agent.sessions.errors import (  # noqa: E402
    SessionCorruptionError,
    SessionMigrationError,
)
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.sessions.legacy_migration import migrate_legacy_session_database  # noqa: E402
from code_agent.sessions.tests._migration_test_support import (  # noqa: E402
    advance_v3_database,
    create_v1_database,
    create_v3_database,
    insert_legacy_checkpoint,
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
        with closing(sqlite3.connect(self.database)) as connection, connection:
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
        with closing(sqlite3.connect(self.database)) as connection, connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, SCHEMA_VERSION)

    def test_v3_database_migrates_task_state_table(self) -> None:
        create_v3_database(self.database)

        SQLiteSessionRepository(self.database)

        with closing(sqlite3.connect(self.database)) as connection, connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertIn("task_states", tables)

    def test_every_historical_schema_version_migrates_to_current_idempotently(self) -> None:
        for version in range(SCHEMA_VERSION):
            database = Path(self.temporary.name) / f"sessions-v{version}.sqlite3"
            with closing(sqlite3.connect(database)) as connection, connection:
                for target in range(1, version + 1):
                    for statement in _MIGRATIONS[target]:
                        connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {version}")

            SQLiteSessionRepository(database)
            SQLiteSessionRepository(database)

            with closing(sqlite3.connect(database)) as connection, connection:
                migrated = connection.execute("PRAGMA user_version").fetchone()[0]
                foreign_keys = {
                    row[2]
                    for row in connection.execute(
                        "PRAGMA foreign_key_list(rewind_operations)"
                    )
                }
            self.assertEqual(migrated, SCHEMA_VERSION, version)
            self.assertTrue(
                {"workspace_lineages", "checkpoints", "tasks"}.issubset(foreign_keys),
                version,
            )

    def test_current_required_indexes_and_columns_are_validated_on_reopen(self) -> None:
        SQLiteSessionRepository(self.database)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(rewind_operations)")
            }
            indexes = {
                row[1]
                for row in connection.execute("PRAGMA index_list(rewind_operations)")
            }
            cursor_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(checkpoint_workspace_state)"
                )
            }
            usage_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(workspace_lineage_usage)"
                )
            }
            peer_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(peer_messages)")
            }
            peer_indexes = {
                row[1]
                for row in connection.execute("PRAGMA index_list(peer_messages)")
            }
        self.assertIn("replacement_task_id", columns)
        self.assertIn("rewind_operations_one_pending", indexes)
        self.assertIn("lineage_id", cursor_columns)
        self.assertIn("last_failure_signature", usage_columns)
        self.assertTrue({"origin", "claim_token", "expires_at"}.issubset(peer_columns))
        self.assertIn("peer_messages_receiver_status_created", peer_indexes)

        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DROP INDEX rewind_operations_status_created")
        with self.assertRaises(SessionCorruptionError):
            SQLiteSessionRepository(self.database)

    def test_future_schema_version_is_rejected_without_mutation(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("PRAGMA user_version = 999")

        with self.assertRaises(SessionMigrationError):
            SQLiteSessionRepository(self.database)

        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 999)

    def test_random_bytes_are_rejected_without_replacement(self) -> None:
        damaged = b"not a sqlite database\x00keep-me"
        self.database.write_bytes(damaged)

        with self.assertRaises(SessionCorruptionError):
            SQLiteSessionRepository(self.database)

        self.assertEqual(self.database.read_bytes(), damaged)

    def test_current_version_with_missing_tables_is_rejected(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        with self.assertRaises(SessionCorruptionError):
            SQLiteSessionRepository(self.database)

    async def test_malformed_record_json_fails_closed(self) -> None:
        repository = SQLiteSessionRepository(self.database)
        thread_id = await repository.create_thread()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "INSERT INTO messages(thread_id, payload, created_at) VALUES (?, ?, ?)",
                (thread_id, "{broken", "2026-07-11T00:00:00Z"),
            )

        with self.assertRaises(SessionCorruptionError):
            await repository.load_messages(thread_id)

    async def test_malformed_task_state_json_fails_closed(self) -> None:
        repository = SQLiteSessionRepository(self.database)
        thread_id = await repository.create_thread()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "INSERT INTO task_states(thread_id, payload, updated_at) VALUES (?, ?, ?)",
                (thread_id, "{broken", "2026-07-11T00:00:00Z"),
            )

        with self.assertRaises(SessionCorruptionError):
            await repository.load_task_state(thread_id)

    def test_failed_migration_rolls_back_schema_and_version(self) -> None:
        create_v1_database(self.database)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE VIEW goals AS SELECT 1 AS value")

        with self.assertRaises(SessionMigrationError):
            SQLiteSessionRepository(self.database)

        with closing(sqlite3.connect(self.database)) as connection, connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(threads)")
            }
        self.assertEqual(version, 1)
        self.assertNotIn("title", columns)

    def test_legacy_database_copy_is_atomic_and_idempotent(self) -> None:
        create_v1_database(self.database)
        with closing(sqlite3.connect(self.database)) as connection, connection:
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
