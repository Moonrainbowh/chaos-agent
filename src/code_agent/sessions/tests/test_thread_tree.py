from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.models import Message  # noqa: E402
from code_agent.sessions.errors import SessionNotFound  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402


class ThreadTreeRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = SQLiteSessionRepository(database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_parent_and_stable_message_records_survive_restart(self) -> None:
        root = await self.repository.create_thread()
        child = await self.repository.create_thread(parent_thread_id=root)
        await self.repository.append_message(child, Message("user", "one"))
        await self.repository.append_message(child, Message("assistant", "two"))

        relation = await self.repository.load_thread_relation(child)
        records = await self.repository.load_message_records(child)

        self.assertEqual(relation.parent_thread_id, root)
        self.assertEqual(relation.child_thread_ids, ())
        self.assertEqual([record.message.content for record in records], ["one", "two"])
        self.assertEqual(
            [record.sequence for record in records],
            sorted(record.sequence for record in records),
        )
        self.assertTrue(all(record.thread_id == child for record in records))

    async def test_root_lists_direct_children(self) -> None:
        root = await self.repository.create_thread()
        first = await self.repository.create_thread(parent_thread_id=root)
        second = await self.repository.create_thread(parent_thread_id=root)

        relation = await self.repository.load_thread_relation(root)

        self.assertEqual(set(relation.child_thread_ids), {first, second})
        self.assertIsNone(relation.parent_thread_id)

    async def test_child_cannot_create_grandchild(self) -> None:
        root = await self.repository.create_thread()
        child = await self.repository.create_thread(parent_thread_id=root)

        with self.assertRaises(ValueError):
            await self.repository.create_thread(parent_thread_id=child)

    async def test_missing_parent_fails_closed(self) -> None:
        with self.assertRaises(SessionNotFound):
            await self.repository.create_thread(parent_thread_id="missing")


if __name__ == "__main__":
    unittest.main()
