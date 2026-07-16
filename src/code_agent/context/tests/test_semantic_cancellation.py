from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from threading import get_ident
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context import builder as builder_module  # noqa: E402
from code_agent.context.builder import WorkspaceContextBuilder  # noqa: E402
from code_agent.context.compaction import DeterministicCompactor  # noqa: E402
from code_agent.context.models import ContextConfig  # noqa: E402
from code_agent.context.repo_map import RepoMapBuilder  # noqa: E402
from code_agent.context.rules import RuleLoader  # noqa: E402
from code_agent.core.cancellation import (  # noqa: E402
    CancellationError,
    CancellationToken,
)
from code_agent.core.context_request import ContextRequest  # noqa: E402
from code_agent.core.models import Message  # noqa: E402
from code_agent.core.task_state import TaskState  # noqa: E402
from code_agent.thread_intelligence.compaction import (  # noqa: E402
    SemanticCompactionResult,
)
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class ReturningSemanticCompactor:
    async def compact(self, thread_id, revision, messages, **kwargs):
        return SemanticCompactionResult(tuple(messages), None, False, False)


class BlockingSemanticCompactor:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def compact(self, thread_id, revision, messages, **kwargs):
        self.entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class SemanticExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.cwd = self.root / "src"
        self.cwd.mkdir()
        (self.root / "AGENTS.md").write_text("Root constraint.", encoding="utf-8")
        self.config = ContextConfig(
            self.root,
            self.cwd,
            "Stable system prefix.",
            repo_scan=100,
            repo_map_tokens=120,
            message_tokens=30,
            recent_messages=2,
        )
        guard = WorkspacePathGuard(self.root)
        files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        self.rules = RuleLoader(guard, files, self.config)
        self.repo_map = RepoMapBuilder(files, self.config)
        self.builder = self.builder_for(ReturningSemanticCompactor())

    def builder_for(self, semantic_compactor) -> WorkspaceContextBuilder:
        return WorkspaceContextBuilder(
            self.config,
            self.rules,
            self.repo_map,
            DeterministicCompactor(self.config),
            semantic_compactor=semantic_compactor,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, cancellation: CancellationToken | None = None) -> ContextRequest:
        return ContextRequest(
            thread_id="thread-a",
            revision=3,
            messages=(Message(role="assistant", content="earlier answer"),),
            user_input="latest request",
            tools=(),
            task_state=TaskState.empty(),
            cancellation=cancellation or CancellationToken(),
        )

    async def test_working_token_scan_stays_off_event_loop_thread(self) -> None:
        event_loop_thread = get_ident()
        scan_threads: list[int] = []
        original = builder_module._message_tokens

        def record_scan(messages):
            scan_threads.append(get_ident())
            return original(messages)

        with patch.object(builder_module, "_message_tokens", side_effect=record_scan):
            await self.builder.build(self.request())

        self.assertTrue(scan_threads)
        self.assertNotIn(event_loop_thread, scan_threads)

    async def test_active_token_cancellation_stops_semantic_work(self) -> None:
        semantic = BlockingSemanticCompactor()
        cancellation = CancellationToken()
        build_task = asyncio.create_task(
            self.builder_for(semantic).build(self.request(cancellation))
        )

        try:
            await asyncio.wait_for(semantic.entered.wait(), 1.0)
            cancellation.cancel("stop active semantic")
            with self.assertRaises(CancellationError) as raised:
                await asyncio.wait_for(build_task, 0.5)
            self.assertEqual(raised.exception.reason, "stop active semantic")
            self.assertTrue(semantic.cancelled.is_set())
        finally:
            if not build_task.done():
                build_task.cancel()
            await asyncio.gather(build_task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
