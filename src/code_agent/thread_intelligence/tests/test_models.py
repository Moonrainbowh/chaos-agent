from __future__ import annotations

import unittest

from code_agent.core.models import Message, Usage
from code_agent.thread_intelligence.models import (
    SemanticCheckpoint,
    SummaryResponse,
    anchor_message,
    source_range_digest,
)


class ThreadIntelligenceModelTests(unittest.TestCase):
    def test_message_anchor_is_stable_and_detects_mutation(self) -> None:
        message = Message(role="user", content="inspect the failure")

        first = anchor_message("thread-a", 3, message)
        second = anchor_message("thread-a", 3, message)
        changed = anchor_message("thread-a", 3, Message(role="user", content="different"))

        self.assertEqual(first, second)
        self.assertNotEqual(first.anchor.digest, changed.anchor.digest)
        self.assertEqual(first.anchor.stable_id, "thread-a:message:3")

    def test_checkpoint_records_traceable_source_range_and_usage(self) -> None:
        sources = (
            anchor_message("thread-a", 0, Message(role="user", content="first")),
            anchor_message("thread-a", 1, Message(role="assistant", content="second")),
        )
        response = SummaryResponse("The user asked; the assistant answered.", "summary-model", Usage(20, 8), 2)

        checkpoint = SemanticCheckpoint.create(sources, response)
        rebuilt = SemanticCheckpoint.create(sources, response)

        self.assertEqual(checkpoint.source_start, sources[0].anchor)
        self.assertEqual(checkpoint.source_end, sources[-1].anchor)
        self.assertEqual(checkpoint.source_digest, source_range_digest(sources))
        self.assertEqual(checkpoint.usage.total_tokens, 28)
        self.assertEqual(checkpoint.version, 2)
        self.assertEqual(checkpoint.id, rebuilt.id)


if __name__ == "__main__":
    unittest.main()
