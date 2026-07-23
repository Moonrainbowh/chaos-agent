from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from code_agent.sessions._database import SCHEMA_VERSION
from code_agent.sessions.repository import SQLiteSessionRepository

try:
    from test_migrations import create_v3_database, insert_legacy_checkpoint
except ModuleNotFoundError:
    from .test_migrations import create_v3_database, insert_legacy_checkpoint


class CheckpointCursorMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_checkpoint_table_gains_cursor_columns(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            database = Path(temporary) / "sessions.sqlite3"
            create_v3_database(database)
            insert_legacy_checkpoint(database)

            repository = SQLiteSessionRepository(database)
            created = await repository.create_checkpoint("legacy", "current")

            with sqlite3.connect(database) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(checkpoints)")
                }

            self.assertEqual(version, SCHEMA_VERSION)
            self.assertTrue(
                {"message_sequence", "event_sequence"}.issubset(columns)
            )
            checkpoints = await repository.list_checkpoints("legacy")
            self.assertEqual(
                [item.id for item in checkpoints], ["checkpoint", created]
            )
            self.assertIsNone(checkpoints[0].message_sequence)
            self.assertIsNone(checkpoints[0].event_sequence)
            self.assertEqual(checkpoints[1].message_sequence, 0)
            self.assertEqual(checkpoints[1].event_sequence, 0)


if __name__ == "__main__":
    unittest.main()
