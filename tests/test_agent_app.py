from __future__ import annotations

import sys
import tempfile
import unittest
from collections.abc import AsyncIterator, Sequence
from io import StringIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent_win.app import RootActionDispatcher  # noqa: E402
from code_agent_win.cli import run  # noqa: E402
from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.engine import AgentEngine  # noqa: E402
from code_agent.core.events import EventKind  # noqa: E402
from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.core.models import ModelEvent, ModelEventKind, ToolCall, ToolDefinition  # noqa: E402
from code_agent.context.builder import WorkspaceContextBuilder  # noqa: E402
from code_agent.context.compaction import DeterministicCompactor  # noqa: E402
from code_agent.context.models import ContextConfig  # noqa: E402
from code_agent.context.repo_map import RepoMapBuilder  # noqa: E402
from code_agent.context.cache import RepoMapCache  # noqa: E402
from code_agent.context.rules import RuleLoader  # noqa: E402
from code_agent.interfaces.terminal_state import ApprovalBroker  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import ApprovalMode  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402


class FakeModel:
    def __init__(self, streams: Sequence[Sequence[ModelEvent]]) -> None:
        self.streams = list(streams)

    def stream(
        self, system_prompt: str, messages: object, tools: Sequence[ToolDefinition]
    ) -> AsyncIterator[ModelEvent]:
        stream = self.streams.pop(0)

        async def generate() -> AsyncIterator[ModelEvent]:
            for event in stream:
                yield event

        return generate()


class RootActionDispatcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "note.txt").write_bytes(b"before\n")
        guard = WorkspacePathGuard(self.root)
        files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        self.dispatcher = RootActionDispatcher(
            files,
            WorkspaceEditor(guard),
            ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=self.root)),
            ApprovalBroker(),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_read_file_uses_typed_workspace_tool(self) -> None:
        result = await self.dispatcher.dispatch(
            ActionRequest("call-1", "read_file", {"path": "note.txt"}),
            CancellationToken(),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.output["text"], "before\n")
        self.assertIn("read_file", [tool.name for tool in self.dispatcher.tools()])

    async def test_write_file_returns_previewed_diff_and_applies_after_policy(self) -> None:
        result = await self.dispatcher.dispatch(
            ActionRequest(
                "call-1",
                "write_file",
                {"path": "note.txt", "content": "after\n"},
            ),
            CancellationToken(),
        )

        self.assertFalse(result.is_error)
        self.assertIn("--- a/note.txt", result.metadata["diff"])
        self.assertEqual((self.root / "note.txt").read_text(encoding="utf-8"), "after\n")

    async def test_successful_write_invalidates_the_exact_relative_cache_path(self) -> None:
        invalidated: list[tuple[str, ...]] = []
        guard = WorkspacePathGuard(self.root)
        dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=self.root)),
            ApprovalBroker(),
            invalidate_cache=lambda paths: invalidated.append(tuple(paths)),
        )

        result = await dispatcher.dispatch(
            ActionRequest("call-1", "write_file", {"path": "note.txt", "content": "after\n"}),
            CancellationToken(),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(invalidated, [("note.txt",)])

    async def test_denied_write_does_not_invalidate_cache(self) -> None:
        invalidated: list[tuple[str, ...]] = []
        guard = WorkspacePathGuard(self.root)
        dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            ActionPolicy(PolicyConfig(ApprovalMode.ASK, workspace_root=self.root)),
            ApprovalBroker(),
            invalidate_cache=lambda paths: invalidated.append(tuple(paths)),
        )

        result = await dispatcher.dispatch(
            ActionRequest("call-1", "write_file", {"path": "note.txt", "content": "after\n"}),
            CancellationToken(),
        )

        self.assertTrue(result.is_error)
        self.assertEqual(invalidated, [])

    async def test_ask_mode_rejects_noninteractive_write_without_waiting(self) -> None:
        guard = WorkspacePathGuard(self.root)
        dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            ActionPolicy(PolicyConfig(ApprovalMode.ASK, workspace_root=self.root)),
            ApprovalBroker(),
        )

        result = await dispatcher.dispatch(
            ActionRequest("call-1", "write_file", {"path": "note.txt", "content": "x"}),
            CancellationToken(),
        )

        self.assertTrue(result.is_error)
        self.assertEqual(result.output["error"], "approval required in TUI")


class CliFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_reports_safe_error_without_a_traceback(self) -> None:
        stderr = StringIO()

        with patch("code_agent_win.cli.create_application", side_effect=RuntimeError("secret detail")):
            with patch("sys.stderr", stderr):
                status = await run(("ask", "inspect"))

        self.assertEqual(status, 1)
        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertNotIn("secret detail", stderr.getvalue())


class FullStackTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_model_read_action_round_trips_through_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.txt").write_bytes(b"hello\n")
            guard = WorkspacePathGuard(root)
            files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
            config = ContextConfig(root, root, "System", repo_scan=100)
            context = WorkspaceContextBuilder(
                config,
                RuleLoader(guard, files, config),
                RepoMapBuilder(files, config),
                DeterministicCompactor(config),
            )
            dispatcher = RootActionDispatcher(
                files,
                WorkspaceEditor(guard),
                ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=root)),
                ApprovalBroker(),
            )
            call = ToolCall("call-1", "read_file", {"path": "note.txt"})
            model = FakeModel(
                (
                    (
                        ModelEvent(ModelEventKind.TOOL_CALL, tool_call=call),
                        ModelEvent(ModelEventKind.COMPLETED),
                    ),
                    (
                        ModelEvent(ModelEventKind.TEXT_DELTA, text="read complete"),
                        ModelEvent(ModelEventKind.COMPLETED),
                    ),
                )
            )
            sessions = SQLiteSessionRepository(root / "sessions.sqlite3")
            engine = AgentEngine(model, context, dispatcher, sessions)

            events = [event async for event in engine.run("read note")]

            thread_id = events[0].payload["thread_id"]
            messages = await sessions.load_messages(thread_id)
            self.assertEqual(events[-1].kind, EventKind.COMPLETED)
            self.assertEqual(messages[-1].content, "read complete")
            self.assertIn("hello", messages[-2].content)


if __name__ == "__main__":
    unittest.main()
