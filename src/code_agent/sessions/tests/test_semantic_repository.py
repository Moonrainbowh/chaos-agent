from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from code_agent.core.models import Message, Usage
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.thread_intelligence.models import (
    SemanticCheckpoint,
    SourceAnchor,
    SourceKind,
    SummaryResponse,
    ThreadEntry,
    anchor_message,
)


class SemanticRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = SQLiteSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_checkpoint_and_index_publish_atomically_and_survive_restart(
        self,
    ) -> None:
        thread_id = await self.repository.create_thread()
        sources = (
            anchor_message(thread_id, 1, Message("user", "needle question")),
            anchor_message(thread_id, 2, Message("assistant", "needle answer")),
        )
        checkpoint = SemanticCheckpoint.create(
            sources, SummaryResponse("needle summary", "model-a", Usage(10, 3))
        )
        summary_anchor = SourceAnchor(
            thread_id,
            SourceKind.CHECKPOINT,
            2,
            checkpoint.id,
            hashlib.sha256(checkpoint.summary.encode("utf-8")).hexdigest(),
        )
        entry = ThreadEntry(summary_anchor, checkpoint.summary)

        await self.repository.publish_semantic_checkpoint(checkpoint, (entry,))
        reopened = SQLiteSessionRepository(self.database)

        self.assertEqual(
            await reopened.load_semantic_checkpoints(thread_id), (checkpoint,)
        )
        hits = await reopened.search_thread_index((thread_id,), "needle", limit=5)
        self.assertEqual(hits[0].entry, entry)
        self.assertEqual(await reopened.read_thread_entry(summary_anchor), entry)

    async def test_duplicate_publish_is_idempotent(self) -> None:
        thread_id = await self.repository.create_thread()
        sources = (
            anchor_message(thread_id, 1, Message("user", "one")),
            anchor_message(thread_id, 2, Message("assistant", "two")),
        )
        checkpoint = SemanticCheckpoint.create(
            sources, SummaryResponse("summary", "model-a", Usage(2, 1))
        )

        await self.repository.publish_semantic_checkpoint(checkpoint, ())
        await self.repository.publish_semantic_checkpoint(checkpoint, ())

        self.assertEqual(
            await self.repository.load_semantic_checkpoints(thread_id), (checkpoint,)
        )


if __name__ == "__main__":
    unittest.main()
