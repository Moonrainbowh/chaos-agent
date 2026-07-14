from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent_win.app import RootActionDispatcher  # noqa: E402
from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.interfaces.terminal_state import ApprovalBroker  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import ApprovalMode  # noqa: E402
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class RootActionDispatcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "note.txt").write_bytes(b"before\n")
        guard = WorkspacePathGuard(self.root)
        files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        self.dispatcher = RootActionDispatcher(files, WorkspaceEditor(guard), ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=self.root)), ApprovalBroker())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_read_file_uses_typed_workspace_tool(self) -> None:
        result = await self.dispatcher.dispatch(ActionRequest("call-1", "read_file", {"path": "note.txt"}), CancellationToken())
        self.assertFalse(result.is_error)
        self.assertEqual(result.output["text"], "before\n")
        self.assertIn("read_file", [tool.name for tool in self.dispatcher.tools()])

    async def test_full_local_reads_and_writes_explicit_external_file(self) -> None:
        outside = self.root.parent / "outside.txt"
        outside.write_bytes(b"external\n")
        guard = WorkspacePathGuard(self.root, allow_outside=True)
        dispatcher = RootActionDispatcher(WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)), WorkspaceEditor(guard), ActionPolicy(PolicyConfig(ApprovalMode.FULL_LOCAL, workspace_root=self.root)), ApprovalBroker())
        read = await dispatcher.dispatch(ActionRequest("read", "read_file", {"path": str(outside)}), CancellationToken())
        write = await dispatcher.dispatch(ActionRequest("write", "write_file", {"path": str(outside), "content": "changed\n"}), CancellationToken())
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
        dispatcher = RootActionDispatcher(WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)), WorkspaceEditor(guard), ActionPolicy(PolicyConfig(ApprovalMode.FULL_LOCAL, workspace_root=self.root)), ApprovalBroker())
        result = await dispatcher.dispatch(ActionRequest("list", "list_files", {"root": str(outside)}), CancellationToken())
        self.assertFalse(result.is_error)
        self.assertEqual(result.output["files"], ("nested/child.txt", "top.txt"))

    async def test_write_file_returns_previewed_diff_and_applies_after_policy(self) -> None:
        result = await self.dispatcher.dispatch(ActionRequest("call-1", "write_file", {"path": "note.txt", "content": "after\n"}), CancellationToken())
        self.assertFalse(result.is_error)
        self.assertIn("--- a/note.txt", result.metadata["diff"])
        self.assertEqual((self.root / "note.txt").read_text(encoding="utf-8"), "after\n")

    async def test_successful_write_invalidates_the_exact_relative_cache_path(self) -> None:
        invalidated: list[tuple[str, ...]] = []
        guard = WorkspacePathGuard(self.root)
        dispatcher = RootActionDispatcher(WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)), WorkspaceEditor(guard), ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=self.root)), ApprovalBroker(), invalidate_cache=lambda paths: invalidated.append(tuple(paths)))
        result = await dispatcher.dispatch(ActionRequest("call-1", "write_file", {"path": "note.txt", "content": "after\n"}), CancellationToken())
        self.assertFalse(result.is_error)
        self.assertEqual(invalidated, [("note.txt",)])

    async def test_successful_replace_invalidates_the_exact_relative_cache_path(self) -> None:
        invalidated: list[tuple[str, ...]] = []
        guard = WorkspacePathGuard(self.root)
        dispatcher = RootActionDispatcher(WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)), WorkspaceEditor(guard), ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=self.root)), ApprovalBroker(), invalidate_cache=lambda paths: invalidated.append(tuple(paths)))
        result = await dispatcher.dispatch(ActionRequest("call-1", "replace_text", {"path": "note.txt", "old_text": "before", "new_text": "after"}), CancellationToken())
        self.assertFalse(result.is_error)
        self.assertEqual(invalidated, [("note.txt",)])

    async def test_denied_write_does_not_invalidate_cache(self) -> None:
        invalidated: list[tuple[str, ...]] = []
        guard = WorkspacePathGuard(self.root)
        dispatcher = RootActionDispatcher(WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)), WorkspaceEditor(guard), ActionPolicy(PolicyConfig(ApprovalMode.ASK, workspace_root=self.root)), ApprovalBroker(), invalidate_cache=lambda paths: invalidated.append(tuple(paths)))
        result = await dispatcher.dispatch(ActionRequest("call-1", "write_file", {"path": "note.txt", "content": "after\n"}), CancellationToken())
        self.assertTrue(result.is_error)
        self.assertEqual(invalidated, [])

    async def test_ask_mode_rejects_noninteractive_write_without_waiting(self) -> None:
        guard = WorkspacePathGuard(self.root)
        dispatcher = RootActionDispatcher(WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)), WorkspaceEditor(guard), ActionPolicy(PolicyConfig(ApprovalMode.ASK, workspace_root=self.root)), ApprovalBroker())
        result = await dispatcher.dispatch(ActionRequest("call-1", "write_file", {"path": "note.txt", "content": "x"}), CancellationToken())
        self.assertTrue(result.is_error)
        self.assertEqual(result.output["error"], "approval required in TUI")
