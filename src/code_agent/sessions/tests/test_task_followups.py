from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.core.models import Message
from code_agent.core.task import TaskAuthorization, TaskContract
from code_agent.sessions.repository import SQLiteSessionRepository


class TaskFollowupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = SQLiteSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_followups_stay_hidden_until_atomic_fifo_promotion(self) -> None:
        thread_id = await self.repository.create_thread()
        task = await self.repository.create_task(
            thread_id,
            TaskContract(
                "repair", TaskAuthorization.local_workspace(self.temporary.name)
            ),
        )
        first = Message("user", "first queued")
        second = Message("user", "second queued")

        first_id = await self.repository.record_task_followup(
            task.id, first, first.content
        )
        second_id = await self.repository.record_task_followup(
            task.id, second, second.content
        )
        reopened = SQLiteSessionRepository(self.database)

        self.assertEqual(await reopened.load_messages(thread_id), ())
        self.assertEqual(
            await reopened.list_task_followups(task.id),
            ((first_id, first), (second_id, second)),
        )
        self.assertEqual(
            await reopened.promote_task_followups(task.id),
            ((first_id, first), (second_id, second)),
        )
        self.assertEqual(await reopened.load_messages(thread_id), (first, second))
        self.assertEqual(await reopened.promote_task_followups(task.id), ())


if __name__ == "__main__":
    unittest.main()
