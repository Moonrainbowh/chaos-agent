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

from code_agent_win.app import RootActionDispatcher, _session_path, create_application  # noqa: E402
from code_agent_win.cli import _split_global_options, _split_profile_option, run  # noqa: E402
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

    async def test_full_local_reads_and_writes_explicit_external_file(self) -> None:
        outside = self.root.parent / "outside.txt"
        outside.write_bytes(b"external\n")
        guard = WorkspacePathGuard(self.root, allow_outside=True)
        dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            ActionPolicy(
                PolicyConfig(ApprovalMode.FULL_LOCAL, workspace_root=self.root)
            ),
            ApprovalBroker(),
        )

        read = await dispatcher.dispatch(
            ActionRequest("read", "read_file", {"path": str(outside)}),
            CancellationToken(),
        )
        write = await dispatcher.dispatch(
            ActionRequest(
                "write", "write_file", {"path": str(outside), "content": "changed\n"}
            ),
            CancellationToken(),
        )

        self.assertFalse(read.is_error)
        self.assertEqual(read.output["text"], "external\n")
        self.assertFalse(write.is_error)
        self.assertEqual(outside.read_text(encoding="utf-8"), "changed\n")

    async def test_full_local_recursively_lists_an_explicit_external_root(self) -> None:
        outside = self.root.parent / f"{self.root.name}-external-tree"
        (outside / "nested").mkdir(parents=True)
        (outside / "top.txt").write_text("top", encoding="utf-8")
        (outside / "nested" / "child.txt").write_text("child", encoding="utf-8")
        guard = WorkspacePathGuard(self.root, allow_outside=True)
        dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            ActionPolicy(
                PolicyConfig(ApprovalMode.FULL_LOCAL, workspace_root=self.root)
            ),
            ApprovalBroker(),
        )

        result = await dispatcher.dispatch(
            ActionRequest("list", "list_files", {"root": str(outside)}),
            CancellationToken(),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.output["files"], ("nested/child.txt", "top.txt"))

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

    async def test_successful_replace_invalidates_the_exact_relative_cache_path(self) -> None:
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
            ActionRequest(
                "call-1",
                "replace_text",
                {"path": "note.txt", "old_text": "before", "new_text": "after"},
            ),
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
    def test_global_profile_option_is_removed_before_command_parsing(self) -> None:
        profile, command = _split_profile_option(("--profile", "company", "ask", "inspect"))

        self.assertEqual(profile, "company")
        self.assertEqual(command, ("ask", "inspect"))
        with self.assertRaises(ValueError):
            _split_profile_option(("--profile", "", "ask", "inspect"))

    def test_global_model_option_is_removed_before_command_parsing(self) -> None:
        model, command = _split_global_options(("--model", "fast", "ask", "inspect"))

        self.assertEqual(model, "fast")
        self.assertEqual(command, ("ask", "inspect"))
        with self.assertRaises(ValueError):
            _split_global_options(("--model", "fast", "--model", "slow", "ask", "inspect"))

    async def test_cli_reports_safe_error_without_a_traceback(self) -> None:
        stderr = StringIO()

        with patch("code_agent_win.cli.create_application", side_effect=RuntimeError("secret detail")):
            with patch("sys.stderr", stderr):
                status = await run(("ask", "inspect"))

        self.assertEqual(status, 1)
        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertNotIn("secret detail", stderr.getvalue())


class ApplicationConstructionTests(unittest.TestCase):
    def test_tui_uses_the_session_repository_for_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with patch("code_agent_win.app._model_client", return_value=object()):
                with patch(
                    "code_agent_win.app._session_path",
                    return_value=root / "sessions.sqlite3",
                ):
                    application = create_application(root)

        self.assertIs(application.tui.sessions, application.tui.history)

    def test_session_path_falls_back_to_the_legacy_data_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "code-agent" / "sessions.sqlite3"
            legacy.parent.mkdir()
            legacy.touch()

            with patch("code_agent_win.app.os.getenv", return_value=str(root)):
                self.assertEqual(_session_path(), legacy)

    def test_session_path_uses_the_chaos_agent_directory_for_new_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            with patch("code_agent_win.app.os.getenv", return_value=str(root)):
                self.assertEqual(_session_path(), root / "chaos-agent" / "sessions.sqlite3")


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
