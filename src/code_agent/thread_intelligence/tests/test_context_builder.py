from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.context.compaction import DeterministicCompactor
from code_agent.context.models import ContextConfig
from code_agent.core.cancellation import CancellationToken
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
    async def build(
        self,
        thread_id: str,
        messages: object,
        user_input: str,
        tools: object,
        task_state: TaskState,
        cancellation: CancellationToken,
    ) -> ContextBundle:
        return ContextBundle(system_prompt="system", messages=messages)


class ThreadAwareContextBuilderTests(unittest.IsolatedAsyncioTestCase):
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
            fallback = DeterministicCompactor(config)
            builder = ThreadAwareContextBuilder(
                repository,
                SemanticCompactor(Summarizer(), fallback, keep_recent=2),
                RecordingContext(),
                context_limit=10,
                target_tokens=1_000,
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


if __name__ == "__main__":
    unittest.main()
