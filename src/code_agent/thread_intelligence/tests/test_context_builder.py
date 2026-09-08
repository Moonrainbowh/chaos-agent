from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.context.compaction import DeterministicCompactor
from code_agent.context.models import ContextConfig
from code_agent.core.cancellation import CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.models import ContextBundle, Message, Usage
from code_agent.core.task_state import TaskState
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.thread_intelligence.compaction import SemanticCompactor
from code_agent.thread_intelligence.context_builder import ThreadAwareContextBuilder
from code_agent.thread_intelligence.models import SummaryResponse


class Summarizer:
    async def summarize(self, request: object, cancellation: CancellationToken) -> object:
        return SummaryResponse("older exchange summary", "frozen-model", Usage(4, 2))


class RecordingContext:
    def __init__(self) -> None:
        self.requests: list[ContextRequest] = []

    async def build(self, request: ContextRequest) -> ContextBundle:
        self.requests.append(request)
        return ContextBundle(system_prompt="system", messages=request.messages)


def _context_builder(
    repository: SQLiteSessionRepository,
    config: ContextConfig,
    inner: RecordingContext,
    *,
    context_limit: int,
) -> ThreadAwareContextBuilder:
    fallback = DeterministicCompactor(config)
    return ThreadAwareContextBuilder(
        repository,
        SemanticCompactor(Summarizer(), fallback, keep_recent=2),
        inner,
        context_limit=context_limit,
        target_tokens=1_000,
    )


class ThreadAwareContextBuilderTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_overlapping_checkpoint_chain_keeps_all_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteSessionRepository(root / "sessions.sqlite3")
            thread_id = await repository.create_thread()
            for index in range(10):
                await repository.append_message(thread_id, Message("user", f"turn {index}"))
            config = ContextConfig(root, root, "system")
            builder = _context_builder(
                repository, config, RecordingContext(), context_limit=10_000
            )
            await builder.compact_context(thread_id)
            for index in range(10, 14):
                await repository.append_message(thread_id, Message("user", f"turn {index}"))
            await builder.compact_context(thread_id)

            bundle = await builder.build(
                thread_id, (), "", (), TaskState.empty(), CancellationToken()
            )

            self.assertEqual(
                sum("Untrusted semantic checkpoint" in item.content for item in bundle.messages),
                2,
            )
            self.assertEqual(tuple(item.content for item in bundle.messages[-2:]), ("turn 12", "turn 13"))

    async def test_manual_compaction_is_persisted_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteSessionRepository(root / "sessions.sqlite3")
            thread_id = await repository.create_thread()
            for index in range(6):
                await repository.append_message(
                    thread_id,
                    Message("user" if index % 2 == 0 else "assistant", f"turn {index}"),
                )
            config = ContextConfig(root, root, "system")
            inner = RecordingContext()
            builder = _context_builder(
                repository, config, inner, context_limit=10_000
            )

            report = await builder.compact_context(thread_id)
            bundle = await builder.build(
                thread_id, (), "", (), TaskState.empty(), CancellationToken()
            )

            self.assertIsNotNone(report.checkpoint_id)
            self.assertEqual(report.before_messages, 6)
            self.assertEqual(report.after_messages, 3)
            self.assertIn("Untrusted semantic checkpoint", bundle.messages[0].content)
            self.assertEqual(tuple(item.content for item in bundle.messages[-2:]), ("turn 4", "turn 5"))

    async def test_repository_messages_drive_semantic_context_and_persistence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteSessionRepository(root / "sessions.sqlite3")
            thread_id = await repository.create_thread()
            for index in range(6):
                await repository.append_message(
                    thread_id,
                    Message("user" if index % 2 == 0 else "assistant", f"message {index}"),
                )
            config = ContextConfig(
                workspace_root=root, cwd=root, system_prompt="system"
            )
            builder = _context_builder(
                repository, config, RecordingContext(), context_limit=10
            )
            cancellation = CancellationToken()

            bundle = await builder.build(
                thread_id,
                (Message("user", "caller supplied stale content"),),
                "duplicate input",
                (),
                TaskState.empty(),
                cancellation,
            )

            self.assertIn("Untrusted semantic checkpoint", bundle.messages[0].content)
            self.assertEqual(bundle.messages[-1].content, "message 5")
            checkpoints = await repository.load_semantic_checkpoints(thread_id)
            self.assertEqual(len(checkpoints), 1)
            self.assertEqual(checkpoints[0].source_start.sequence, 1)

    async def test_structured_request_metadata_is_preserved_for_inner_context(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteSessionRepository(root / "sessions.sqlite3")
            thread_id = await repository.create_thread()
            durable = Message("user", "persisted request")
            await repository.append_message(thread_id, durable)
            config = ContextConfig(
                workspace_root=root, cwd=root, system_prompt="system"
            )
            inner = RecordingContext()
            builder = _context_builder(
                repository, config, inner, context_limit=10_000
            )
            cancellation = CancellationToken()
            request = ContextRequest(
                thread_id=thread_id,
                revision=7,
                messages=(Message("user", "caller supplied stale content"),),
                user_input="duplicate input",
                tools=(),
                task_state=TaskState.empty(),
                cancellation=cancellation,
                mode_snapshot={"mode": "plan"},
                permission_snapshot={"network": False},
                budget_lease={"model_turns": 3, "max_model_turns": 8},
            )

            bundle = await builder.build(request)

            self.assertEqual(len(inner.requests), 1)
            delegated = inner.requests[0]
            self.assertEqual(delegated.thread_id, thread_id)
            self.assertEqual(delegated.revision, 7)
            self.assertEqual(dict(delegated.mode_snapshot), {"mode": "plan"})
            self.assertEqual(
                dict(delegated.permission_snapshot), {"network": False}
            )
            self.assertEqual(
                dict(delegated.budget_lease),
                {"model_turns": 3, "max_model_turns": 8},
            )
            self.assertIs(delegated.cancellation, cancellation)
            self.assertEqual(delegated.user_input, "")
            self.assertEqual(delegated.messages, (durable,))
            self.assertEqual(bundle.messages, (durable,))


if __name__ == "__main__":
    unittest.main()
