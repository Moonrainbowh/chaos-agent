from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.models import Message  # noqa: E402
from code_agent.sessions.errors import SessionStorageError  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402


class SessionConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.first = SQLiteSessionRepository(self.database)
        self.second = SQLiteSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_two_instances_append_concurrently_without_lost_messages(self) -> None:
        thread_id = await self.first.create_thread()

        async def append(index: int) -> None:
            repository = self.first if index % 2 else self.second
            await repository.append_message(
                thread_id, Message("user", f"message-{index}")
            )

        await asyncio.gather(*(append(index) for index in range(40)))

        messages = await self.first.load_messages(thread_id)
        self.assertEqual(len(messages), 40)
        self.assertEqual(
            {message.content for message in messages},
            {f"message-{index}" for index in range(40)},
        )

    async def test_failed_thread_update_rolls_back_message_insert(self) -> None:
        thread_id = await self.first.create_thread()
        with sqlite3.connect(self.database) as connection:
            connection.executescript(
                """
                CREATE TRIGGER reject_thread_update
                BEFORE UPDATE ON threads
                BEGIN
                    SELECT RAISE(ABORT, 'update rejected');
                END;
                """
            )

        with self.assertRaises(SessionStorageError):
            await self.first.append_message(thread_id, Message("user", "rolled back"))

        self.assertEqual(await self.first.load_messages(thread_id), ())

    async def test_concurrent_thread_creation_produces_stable_unique_ids(self) -> None:
        identifiers = await asyncio.gather(
            *(self.first.create_thread() for _ in range(20))
        )

        self.assertEqual(len(set(identifiers)), 20)
        self.assertTrue(all(len(identifier) == 32 for identifier in identifiers))


if __name__ == "__main__":
    unittest.main()
