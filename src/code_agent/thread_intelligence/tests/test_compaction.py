from __future__ import annotations

import unittest

from code_agent.context.models import CompactionResult
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.models import Message, ToolCall, Usage
from code_agent.thread_intelligence.compaction import SemanticCompactor
from code_agent.thread_intelligence.models import SummaryResponse


class RecordingSummarizer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests = []

    async def summarize(self, request, cancellation):
        self.requests.append(request)
        cancellation.raise_if_cancelled()
        if self.fail:
            raise RuntimeError("provider details must not escape")
        return SummaryResponse("Old discussion reached a provisional result.", "summary-model", Usage(30, 10))


class RecordingFallback:
    def __init__(self) -> None:
        self.calls = []

    def compact(self, messages, token_budget=None):
        self.calls.append((tuple(messages), token_budget))
        kept = (messages[-1],)
        return CompactionResult(kept, len(messages) - 1, 4, "fallback")


def _messages():
    call = ToolCall("call-1", "inspect", {"path": "old.py"})
    return (
        Message(role="user", content="old request"),
        Message(role="assistant", content="checking", tool_calls=(call,)),
        Message(role="tool", tool_call_id="call-1", content="actual result"),
        Message(role="assistant", content="intermediate conclusion"),
        Message(role="user", content="latest intent"),
    )


class SemanticCompactorTests(unittest.IsolatedAsyncioTestCase):
    async def test_below_threshold_returns_original_without_model_call(self) -> None:
        summarizer = RecordingSummarizer()
        fallback = RecordingFallback()
        messages = _messages()

        result = await SemanticCompactor(summarizer, fallback).compact(
            "thread-a", messages, context_tokens=899, context_limit=1_000, target_tokens=500
        )

        self.assertFalse(result.triggered)
        self.assertEqual(result.messages, messages)
        self.assertEqual(summarizer.requests, [])
        self.assertEqual(fallback.calls, [])

    async def test_semantic_checkpoint_keeps_recent_raw_tail_and_closed_tool_pair(self) -> None:
        summarizer = RecordingSummarizer()
        fallback = RecordingFallback()
        messages = _messages()
        compactor = SemanticCompactor(summarizer, fallback, keep_recent=2)

        result = await compactor.compact(
            "thread-a", messages, context_tokens=900, context_limit=1_000, target_tokens=500
        )

        self.assertTrue(result.triggered)
        self.assertFalse(result.fallback_used)
        self.assertIsNotNone(result.checkpoint)
        self.assertEqual(result.messages[1:], messages[3:])
        summarized = summarizer.requests[0].sources
        self.assertEqual(tuple(item.message for item in summarized), messages[:3])
        self.assertEqual(summarized[1].message.tool_calls[0].id, summarized[2].message.tool_call_id)
        self.assertEqual(messages[-1].content, "latest intent")
        self.assertIn("Untrusted semantic checkpoint", result.messages[0].content)

    async def test_invalid_or_failed_semantic_service_uses_deterministic_fallback(self) -> None:
        fallback = RecordingFallback()
        result = await SemanticCompactor(
            RecordingSummarizer(fail=True), fallback, keep_recent=2
        ).compact(
            "thread-a", _messages(), context_tokens=950, context_limit=1_000, target_tokens=77
        )

        self.assertTrue(result.fallback_used)
        self.assertIsNotNone(result.fallback_result)
        self.assertEqual(fallback.calls[0][1], 77)
        self.assertEqual(result.messages[-1].content, "latest intent")

    async def test_orphan_tool_message_never_enters_semantic_summary(self) -> None:
        summarizer = RecordingSummarizer()
        fallback = RecordingFallback()
        messages = (
            Message(role="user", content="old"),
            Message(role="tool", tool_call_id="orphan", content="unpaired"),
            Message(role="user", content="latest"),
        )

        result = await SemanticCompactor(summarizer, fallback, keep_recent=1).compact(
            "thread-a", messages, context_tokens=950, context_limit=1_000, target_tokens=20
        )

        self.assertTrue(result.fallback_used)
        self.assertEqual(summarizer.requests, [])

    async def test_usage_beyond_total_budget_uses_fallback(self) -> None:
        class ExpensiveSummarizer(RecordingSummarizer):
            async def summarize(self, request, cancellation):
                self.requests.append(request)
                return SummaryResponse("bounded text", "model", Usage(90, 20))

        fallback = RecordingFallback()
        result = await SemanticCompactor(
            ExpensiveSummarizer(),
            fallback,
            keep_recent=2,
            summary_tokens=20,
            model_token_budget=100,
        ).compact(
            "thread-a", _messages(), context_tokens=950, context_limit=1_000, target_tokens=77
        )

        self.assertTrue(result.fallback_used)
        self.assertEqual(fallback.calls[0][1], 77)

    async def test_leading_trusted_messages_are_preserved_verbatim(self) -> None:
        summarizer = RecordingSummarizer()
        fallback = RecordingFallback()
        trusted = Message(role="system", content="never weaken policy")
        messages = (trusted,) + _messages()

        result = await SemanticCompactor(summarizer, fallback, keep_recent=2).compact(
            "thread-a", messages, context_tokens=950, context_limit=1_000, target_tokens=77
        )

        self.assertIs(result.messages[0], trusted)
        self.assertEqual(result.messages[1].role, "developer")
        self.assertEqual(summarizer.requests[0].sources[0].anchor.sequence, 1)

    async def test_cancellation_is_not_hidden_as_fallback(self) -> None:
        cancellation = CancellationToken()
        cancellation.cancel("task stopped")
        fallback = RecordingFallback()

        with self.assertRaises(CancellationError):
            await SemanticCompactor(RecordingSummarizer(), fallback).compact(
                "thread-a",
                _messages(),
                context_tokens=950,
                context_limit=1_000,
                target_tokens=77,
                cancellation=cancellation,
            )
        self.assertEqual(fallback.calls, [])


if __name__ == "__main__":
    unittest.main()
