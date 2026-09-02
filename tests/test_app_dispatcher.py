from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent_win.app import RootActionDispatcher, create_application  # noqa: E402
from code_agent.config.loader import load_runtime_config  # noqa: E402
from code_agent.core.action_execution import ActionExecutionContext  # noqa: E402
from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.models import ActionRequest, ActionResult  # noqa: E402
from code_agent.core.task import TaskAuthorization  # noqa: E402
from code_agent.interfaces.terminal_state import ApprovalBroker  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import ApprovalMode  # noqa: E402
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent.verification.python_adapter import PythonVerificationAdapter  # noqa: E402
from tests.test_agent_app_full_stack import _RecordingRuntime  # noqa: E402
from tests.test_rewind_plugin_integration import _AllowPolicy  # noqa: E402


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

    async def test_root_dispatcher_accepts_core_execution_context(self) -> None:
        context = ActionExecutionContext("owner", "origin", "call-context")

        result = await self.dispatcher.dispatch(
            ActionRequest("call-context", "read_file", {"path": "note.txt"}),
            CancellationToken(),
            execution_context=context,
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.output["text"], "before\n")

    async def test_read_only_dispatch_still_accepts_none_context(self) -> None:
        result = await self.dispatcher.dispatch(
            ActionRequest("call-none", "read_file", {"path": "note.txt"}),
            CancellationToken(),
            execution_context=None,
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.output["text"], "before\n")

    async def test_list_files_is_bounded_and_reports_display_metadata(self) -> None:
        discovered = tuple(f"src/file-{index}.py" for index in range(201))
        with patch.object(
            self.dispatcher.files,
            "list_files",
            return_value=discovered,
        ) as listing:
            result = await self.dispatcher.dispatch(
                ActionRequest("list-bounded", "list_files", {"root": "."}),
                CancellationToken(),
            )

        listing.assert_called_once_with(
            ".",
            max_entries=201,
            max_scanned_entries=200_000,
        )
        self.assertFalse(result.is_error)
        self.assertEqual(len(result.output["files"]), 200)
        self.assertTrue(result.output["truncated"])
        self.assertEqual(result.metadata["count"], 200)
        self.assertTrue(result.metadata["truncated"])
        self.assertIn("duration_ms", result.metadata)

    async def test_legacy_mcp_without_risk_api_runs_when_capture_is_disabled(self) -> None:
        class Mcp:
            def definitions(self):
                return ()

            async def call(self, name, arguments):
                return "read"

        guard = WorkspacePathGuard(self.root)
        dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            ActionPolicy(PolicyConfig(
                ApprovalMode.FULL_LOCAL,
                workspace_root=self.root,
                mcp_risks={"mcp.demo.read": "read"},
            )),
            ApprovalBroker(),
            mcp=Mcp(),
        )
        result = await dispatcher.dispatch(
            ActionRequest("mcp-read", "mcp.demo.read", {}),
            CancellationToken(),
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.output["result"], "read")

    async def test_full_local_approves_explicit_external_file_once_per_action(self) -> None:
        outside = self.root.parent / "outside.txt"
        outside.write_bytes(b"external\n")
        guard = WorkspacePathGuard(self.root, allow_outside=True)
        approvals = ApprovalBroker()
        dispatcher = RootActionDispatcher(WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)), WorkspaceEditor(guard), ActionPolicy(PolicyConfig(ApprovalMode.FULL_LOCAL, workspace_root=self.root)), approvals)
        dispatcher.interactive = True

        async def approve(request: ActionRequest) -> ActionResult:
            running = asyncio.create_task(
                dispatcher.dispatch(request, CancellationToken())
            )
            approval = await approvals.next_request()
            self.assertEqual(approval.risk, "high")
            approvals.resolve(approval.request_id, True)
            return await running

        read = await approve(ActionRequest("read", "read_file", {"path": str(outside)}))
        write = await approve(ActionRequest("write", "write_file", {"path": str(outside), "content": "changed\n"}))
        self.assertFalse(read.is_error)
        self.assertEqual(read.output["text"], "external\n")
        self.assertFalse(write.is_error)
        self.assertEqual(outside.read_text(encoding="utf-8"), "changed\n")

    async def test_external_full_local_write_never_enters_snapshot(self) -> None:
        class Capture:
            async def apply_edit(self, *args):
                raise AssertionError("external path entered capture")

        outside = self.root.parent / "capture-external.txt"
        outside.write_text("before", encoding="utf-8")
        guard = WorkspacePathGuard(self.root, allow_outside=True)
        approvals = ApprovalBroker()
        dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            ActionPolicy(PolicyConfig(
                ApprovalMode.FULL_LOCAL, workspace_root=self.root,
            )),
            approvals,
            capture=Capture(),
        )
        dispatcher.interactive = True
        request = ActionRequest(
            "external", "write_file",
            {"path": str(outside), "content": "after"},
        )
        running = asyncio.create_task(
            dispatcher.dispatch(request, CancellationToken())
        )
        approval = await approvals.next_request()
        approvals.resolve(approval.request_id, True)
        result = await running
        self.assertFalse(result.is_error)
        self.assertEqual(outside.read_text(encoding="utf-8"), "after")

    async def test_production_factory_guard_rejects_external_write(self) -> None:
        outside = self.root.parent / "strict-external.txt"
        product_temp = tempfile.TemporaryDirectory(dir=self.root.parent)
        self.addCleanup(product_temp.cleanup)
        product_state = Path(product_temp.name)
        runtime = load_runtime_config(env={
            "CHAOS_CONFIG": str(self.root / "missing.toml"),
            "CHAOS_API": "responses",
            "CHAOS_BASE_URL": "https://api.example.test",
            "CHAOS_MODEL": "test",
            "CHAOS_API_KEY_ENV": "KEY",
            "CHAOS_APPROVAL_MODE": "full-local",
        })
        with patch.dict("os.environ", {
            "USERPROFILE": str(self.root / "profile"),
            "LOCALAPPDATA": str(self.root / "localappdata"),
        }, clear=True), patch(
            "code_agent_win.app._model_client", return_value=object()
        ), patch(
            "code_agent_win.app._session_path",
            return_value=self.root / "sessions.sqlite3",
        ), patch(
            "code_agent_win.app._product_state_root", return_value=product_state,
        ), patch("code_agent_win.app.load_runtime_config", return_value=runtime):
            application = create_application(self.root)
        self.addAsyncCleanup(application.aclose)
        result = await application.dispatcher.dispatch(
            ActionRequest(
                "strict", "write_file",
                {"path": str(outside), "content": "no"},
            ),
            CancellationToken(),
        )
        self.assertFalse(application.dispatcher.editor.guard.allow_outside)
        self.assertTrue(result.is_error)
        self.assertFalse(outside.exists())

    async def test_plugin_verification_records_unknown_gap_once(self) -> None:
        class Capture:
            calls = 0

            async def record_gap(self, context, request, reason):
                self.calls += 1

        class Plugins:
            def targets(self):
                return {"plugin.demo": "run_verification"}

            def risk_map(self):
                return {"plugin.demo": "write"}

            def definitions(self):
                return ()

        guard, capture = WorkspacePathGuard(self.root), Capture()
        dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            _AllowPolicy(),
            ApprovalBroker(),
            runtime=_RecordingRuntime((0,)),
            verification=PythonVerificationAdapter(self.root),
            plugins=Plugins(),
            capture=capture,
        )
        result = await dispatcher.dispatch(
            ActionRequest(
                "plugin-verify", "plugin.demo", {"kind": "python_unittest"},
            ),
            CancellationToken(),
            execution_context=ActionExecutionContext(
                "owner", "origin", "plugin-verify",
            ),
        )
        self.assertFalse(result.is_error)
        self.assertEqual(capture.calls, 1)

    async def test_full_local_recursively_lists_an_explicit_external_root(self) -> None:
        outside = self.root.parent / f"{self.root.name}-external-tree"
        (outside / "nested").mkdir(parents=True)
        (outside / "top.txt").write_text("top", encoding="utf-8")
        (outside / "nested" / "child.txt").write_text("child", encoding="utf-8")
        guard = WorkspacePathGuard(self.root, allow_outside=True)
        approvals = ApprovalBroker()
        dispatcher = RootActionDispatcher(WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)), WorkspaceEditor(guard), ActionPolicy(PolicyConfig(ApprovalMode.FULL_LOCAL, workspace_root=self.root)), approvals)
        dispatcher.interactive = True
        running = asyncio.create_task(
            dispatcher.dispatch(
                ActionRequest("list", "list_files", {"root": str(outside)}),
                CancellationToken(),
            )
        )
        approval = await approvals.next_request()
        approvals.resolve(approval.request_id, True)
        result = await running
        self.assertFalse(result.is_error)
        self.assertEqual(result.output["files"], ("nested/child.txt", "top.txt"))
