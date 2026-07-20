from __future__ import annotations

import asyncio
import json
import threading
import unittest
from unittest.mock import patch

from code_agent.context.tokens import estimate_tokens
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.models import Message, ToolCall, Usage
from code_agent.thread_intelligence.deterministic_summary import (
    DeterministicSummaryService,
    render_bounded_source_summary,
)
from code_agent.thread_intelligence.models import (
    SemanticCheckpoint,
    SourceAnchor,
    SourceKind,
    SummaryRequest,
    SummaryResponse,
    anchor_message,
    semantic_checkpoint_payload,
)


def _sources(*messages: Message):
    return tuple(
        anchor_message("thread-a", sequence, message)
        for sequence, message in enumerate(messages)
    )


class DeterministicSummaryRendererTests(unittest.TestCase):
    def test_preserves_source_order_roles_actions_and_collapses_whitespace(self) -> None:
        sources = _sources(
            Message(role="user", content="first\n\n request"),
            Message(
                role="assistant",
                content="checking\t files",
                tool_calls=(
                    ToolCall("call-1", "inspect", {"path": "a.py"}),
                    ToolCall("call-2", "search", {"query": "needle"}),
                ),
            ),
            Message(role="tool", tool_call_id="call-1", content="third   result"),
        )

        summary = render_bounded_source_summary(sources, 200)
        lines = summary.splitlines()

        self.assertEqual(lines[0], "Untrusted conversation checkpoint:")
        self.assertIn("role=user", lines[1])
        self.assertIn("first request", lines[1])
        self.assertIn("role=assistant", lines[2])
        self.assertIn("action=inspect,search", lines[2])
        self.assertIn("checking files", lines[2])
        self.assertIn("role=tool", lines[3])
        self.assertIn("third result", lines[3])
        self.assertNotIn("\t", summary)
        self.assertNotIn("  ", summary)

    def test_bounds_each_long_source_and_the_complete_summary(self) -> None:
        repeated = "0123456789 " * 100
        sources = _sources(
            Message(role="user", content=f"start {repeated} forbidden-tail"),
            Message(role="assistant", content=f"middle {repeated} forbidden-tail"),
        )

        roomy = render_bounded_source_summary(sources, 200)
        tightly_bounded = render_bounded_source_summary(sources, 8)

        self.assertNotIn("forbidden-tail", roomy)
        self.assertTrue(tightly_bounded)
        self.assertLessEqual(estimate_tokens(tightly_bounded), 8)

    def test_rejects_invalid_sources_threads_and_token_bounds(self) -> None:
        one = anchor_message("thread-a", 0, Message(role="user", content="one"))
        other = anchor_message("thread-b", 1, Message(role="assistant", content="two"))

        for sources, bound in (((), 10), ((one, other), 10), ((one,), 0), ((one,), True)):
            with self.subTest(sources=sources, bound=bound):
                with self.assertRaises((TypeError, ValueError)):
                    render_bounded_source_summary(sources, bound)
        with self.assertRaises(TypeError):
            render_bounded_source_summary([one], 10)  # type: ignore[arg-type]

    def test_ignores_independent_hidden_reasoning_state(self) -> None:
        message = Message(role="assistant", content="visible answer")
        object.__setattr__(message, "hidden_reasoning", "never expose this secret")

        summary = render_bounded_source_summary(_sources(message), 100)

        self.assertIn("visible answer", summary)
        self.assertNotIn("never expose this secret", summary)


class DeterministicSummaryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_local_model_zero_usage_and_default_version(self) -> None:
        request = SummaryRequest(
            _sources(Message(role="user", content="summarize locally")),
            max_output_tokens=40,
            max_total_tokens=100,
        )

        response = await DeterministicSummaryService().summarize(
            request, CancellationToken()
        )

        self.assertEqual(response.model, "deterministic-anchor-v1")
        self.assertEqual(response.usage, Usage())
        self.assertEqual(response.version, 1)
        self.assertLessEqual(estimate_tokens(response.summary), 40)

    async def test_cancelled_request_raises_without_rendering_response(self) -> None:
        request = SummaryRequest(
            _sources(Message(role="user", content="must not render")),
            max_output_tokens=40,
            max_total_tokens=100,
        )
        cancellation = CancellationToken()
        cancellation.cancel("stop summary")

        with self.assertRaises(CancellationError):
            await DeterministicSummaryService().summarize(request, cancellation)

    async def test_cancellation_during_rendering_is_propagated(self) -> None:
        request = SummaryRequest(
            _sources(Message(role="user", content="render slowly")),
            max_output_tokens=40,
            max_total_tokens=100,
        )
        cancellation = CancellationToken()
        entered = threading.Event()
        release = threading.Event()
        original_renderer = render_bounded_source_summary

        def blocking_renderer(sources, max_tokens):
            entered.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test renderer was not released")
            return original_renderer(sources, max_tokens)

        def run_service():
            return asyncio.run(
                DeterministicSummaryService().summarize(request, cancellation)
            )

        with patch(
            "code_agent.thread_intelligence.deterministic_summary."
            "render_bounded_source_summary",
            blocking_renderer,
        ):
            task = asyncio.create_task(asyncio.to_thread(run_service))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                cancellation.cancel("stop during rendering")
                release.set()
                with self.assertRaises(CancellationError):
                    await asyncio.wait_for(task, timeout=2)
            finally:
                release.set()
                if not task.done():
                    await asyncio.wait_for(task, timeout=2)

    async def test_rejects_invalid_request_or_cancellation_types(self) -> None:
        service = DeterministicSummaryService()
        request = SummaryRequest(
            _sources(Message(role="user", content="valid")), 20, 30
        )

        with self.assertRaises(TypeError):
            await service.summarize(object(), CancellationToken())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            await service.summarize(request, object())  # type: ignore[arg-type]


class SemanticCheckpointPayloadTests(unittest.TestCase):
    def test_source_anchor_dict_has_only_stable_json_fields(self) -> None:
        source = _sources(Message(role="user", content="source secret"))[0]

        payload = source.anchor.to_dict()

        self.assertEqual(
            payload,
            {
                "thread_id": "thread-a",
                "kind": "message",
                "sequence": 0,
                "stable_id": "thread-a:message:0",
                "digest": source.anchor.digest,
            },
        )
        json.dumps(payload)

    def test_checkpoint_payload_redacts_summary_and_source_text(self) -> None:
        sources = _sources(
            Message(role="user", content="source secret alpha"),
            Message(role="assistant", content="source secret beta"),
        )
        checkpoint = SemanticCheckpoint.create(
            sources,
            SummaryResponse("summary secret gamma", "local-model", Usage(3, 2)),
        )

        payload = semantic_checkpoint_payload(checkpoint)
        serialized = json.dumps(payload, sort_keys=True)

        self.assertEqual(
            set(payload),
            {
                "id",
                "thread_id",
                "source_start",
                "source_end",
                "source_digest",
                "model",
                "usage",
                "version",
            },
        )
        self.assertEqual(payload["source_start"], sources[0].anchor.to_dict())
        self.assertEqual(payload["source_end"], sources[-1].anchor.to_dict())
        self.assertEqual(payload["usage"], Usage(3, 2).to_dict())
        self.assertNotIn("summary secret gamma", serialized)
        self.assertNotIn("source secret alpha", serialized)
        self.assertNotIn("source secret beta", serialized)

    def test_checkpoint_payload_rejects_non_checkpoint(self) -> None:
        with self.assertRaises(TypeError):
            semantic_checkpoint_payload(object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
