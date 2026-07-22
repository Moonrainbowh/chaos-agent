from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from code_agent.core.models import Message, Usage
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.thread_intelligence.authorization import ThreadAuthorization
from code_agent.thread_intelligence.index import ThreadAccessError
from code_agent.thread_intelligence.models import (
    SemanticCheckpoint,
    SourceAnchor,
    SourceKind,
    SummaryResponse,
    ThreadEntry,
    anchor_message,
)
from code_agent.thread_intelligence.tools import ThreadIntelligenceTools


class ThreadIntelligenceToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_schemas_omit_caller_identity_and_host_scope_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteSessionRepository(
                Path(directory) / "sessions.sqlite3"
            )
            root = await repository.create_thread()
            first = await repository.create_thread(parent_thread_id=root)
            second = await repository.create_thread(parent_thread_id=root)
            entry = await _publish(repository, second, "private needle")
            tools = ThreadIntelligenceTools(
                repository, ThreadAuthorization(repository)
            )

            definitions = tools.definitions()

            self.assertEqual(
                {definition.name for definition in definitions},
                {"search_threads", "read_thread"},
            )
            self.assertNotIn("caller_thread_id", str(definitions))
            root_hits = await tools.search(root, "needle", thread_id=second)
            self.assertEqual(root_hits[0].entry, entry)
            with self.assertRaises(ThreadAccessError):
                await tools.search(first, "needle", thread_id=second)
            with self.assertRaises(ThreadAccessError):
                await tools.read(first, entry.anchor)


async def _publish(
    repository: SQLiteSessionRepository, thread_id: str, text: str
) -> ThreadEntry:
    sources = (
        anchor_message(thread_id, 1, Message("user", "question")),
        anchor_message(thread_id, 2, Message("assistant", "answer")),
    )
    checkpoint = SemanticCheckpoint.create(
        sources, SummaryResponse(text, "model-a", Usage(3, 2))
    )
    anchor = SourceAnchor(
        thread_id,
        SourceKind.CHECKPOINT,
        2,
        checkpoint.id,
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    entry = ThreadEntry(anchor, text)
    await repository.publish_semantic_checkpoint(checkpoint, (entry,))
    return entry


if __name__ == "__main__":
    unittest.main()
