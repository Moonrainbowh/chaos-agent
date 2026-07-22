from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.sessions.repository import SQLiteSessionRepository


class SkillActivationRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_list_and_remove_thread_skill_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "sessions.sqlite3"
            repository = SQLiteSessionRepository(database)
            thread_id = await repository.create_thread()

            await repository.save_skill_activation(
                thread_id, "review", "workspace", "digest-a"
            )
            await repository.save_skill_activation(
                thread_id, "review", "workspace", "digest-b"
            )
            reopened = SQLiteSessionRepository(database)

            records = await reopened.list_skill_activations(thread_id)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].digest, "digest-b")
            await reopened.remove_skill_activation(thread_id, "review")
            self.assertEqual(
                await reopened.list_skill_activations(thread_id), ()
            )


if __name__ == "__main__":
    unittest.main()
