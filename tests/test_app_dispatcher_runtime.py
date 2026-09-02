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
from code_agent.core.models import ActionRequest, ActionResult  # noqa: E402
from code_agent.core.task import TaskAuthorization  # noqa: E402
from code_agent.interfaces.terminal_state import ApprovalBroker  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import ApprovalMode  # noqa: E402
from code_agent.runtime.models import CommandResult, TerminationReason  # noqa: E402
from code_agent.verification.local_adapter import LocalVerificationAdapter  # noqa: E402
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class RootActionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "note.txt").write_bytes(b"before\n")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_write_file_returns_previewed_diff_and_applies_after_policy(self) -> None:
        guard = WorkspacePathGuard(self.root)
        dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=self.root)),
            ApprovalBroker(),
        )
        result = await dispatcher.dispatch(
            ActionRequest(
                "call-1", "write_file", {"path": "note.txt", "content": "after\n"}
            ),
            CancellationToken(),
        )
        self.assertFalse(result.is_error)
        self.assertIn("--- a/note.txt", result.metadata["diff"])
        self.assertEqual(
            (self.root / "note.txt").read_text(encoding="utf-8"), "after\n"
        )

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
            ActionRequest(
                "call-1", "write_file", {"path": "note.txt", "content": "after\n"}
            ),
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
            ActionRequest(
                "call-1", "write_file", {"path": "note.txt", "content": "after\n"}
            ),
            CancellationToken(),
        )
        self.assertTrue(result.is_error)
        self.assertEqual(invalidated, [])

    async def test_completed_command_invalidates_workspace_derived_caches(self) -> None:
        class Runtime:
            async def run(self, spec, cancellation, sink):
                return _successful_command("powershell", "Set-Content generated.txt x")

        invalidated: list[tuple[str, ...]] = []
        guard = WorkspacePathGuard(self.root)
        dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            ActionPolicy(
                PolicyConfig(ApprovalMode.FULL_LOCAL, workspace_root=self.root)
            ),
            ApprovalBroker(),
            runtime=Runtime(),
            invalidate_cache=lambda paths: invalidated.append(tuple(paths)),
        )

        result = await dispatcher.dispatch(
            ActionRequest(
                "call-1", "run_command", {"command": "Set-Content generated.txt x"}
            ),
            CancellationToken(),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(invalidated, [()])

    async def test_unrestricted_command_runs_without_requesting_approval(self) -> None:
        class Runtime:
            async def run(self, spec, cancellation, sink):
                return _successful_command("powershell", "Get-Date", stdout=b"ok")

        approvals = ApprovalBroker()
        guard = WorkspacePathGuard(
            self.root, allow_outside=True, allow_sensitive=True
        )
        dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            ActionPolicy(
                PolicyConfig(ApprovalMode.UNRESTRICTED, workspace_root=self.root)
            ),
            approvals,
            runtime=Runtime(),
        )
        dispatcher.interactive = True

        result = await dispatcher.dispatch(
            ActionRequest("call-free", "run_command", {"command": "Get-Date"}),
            CancellationToken(),
        )

        self.assertFalse(result.is_error)
        self.assertFalse(approvals.resolve("call-free", True))

    async def test_completed_verification_invalidates_workspace_derived_caches(self) -> None:
        class Runtime:
            async def run(self, spec, cancellation, sink):
                return _successful_command("python", "python -m compileall .")

        invalidated: list[tuple[str, ...]] = []
        guard = WorkspacePathGuard(self.root)
        dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=self.root)),
            ApprovalBroker(),
            runtime=Runtime(),
            verification=LocalVerificationAdapter(self.root),
            invalidate_cache=lambda paths: invalidated.append(tuple(paths)),
        )

        result = await dispatcher.dispatch(
            ActionRequest(
                "call-1", "run_verification", {"kind": "python_compileall"}
            ),
            CancellationToken(),
            TaskAuthorization.local_workspace(str(self.root)),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(invalidated, [()])

    async def test_ask_mode_rejects_noninteractive_write_without_waiting(self) -> None:
        guard = WorkspacePathGuard(self.root)
        dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            ActionPolicy(PolicyConfig(ApprovalMode.ASK, workspace_root=self.root)),
            ApprovalBroker(),
        )
        result = await dispatcher.dispatch(
            ActionRequest(
                "call-1", "write_file", {"path": "note.txt", "content": "x"}
            ),
            CancellationToken(),
        )
        self.assertTrue(result.is_error)
        self.assertEqual(result.output["error"], "approval required in TUI")

    async def test_delegate_agent_is_a_policy_checked_typed_action(self) -> None:
        class Subagents:
            async def dispatch(self, request, cancellation):
                self.seen = request
                return ActionResult(
                    request.id,
                    request.name,
                    {"advisory": True, "summary": "reviewed"},
                )

        subagents = Subagents()
        guard = WorkspacePathGuard(self.root)
        policy = ActionPolicy(
            PolicyConfig(
                ApprovalMode.FULL_LOCAL,
                workspace_root=self.root,
                mcp_risks={"delegate_agent": "write"},
            )
        )
        dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),
            policy,
            ApprovalBroker(),
            subagents=subagents,
        )

        result = await dispatcher.dispatch(
            ActionRequest(
                "child-1",
                "delegate_agent",
                {"objective": "review current change", "role": "review"},
            ),
            CancellationToken(),
        )

        self.assertFalse(result.is_error)
        self.assertTrue(result.output["advisory"])
        self.assertEqual(subagents.seen.name, "delegate_agent")


def _successful_command(
    program: str, display_command: str, *, stdout: bytes = b""
) -> CommandResult:
    return CommandResult(
        argv=(program,),
        display_command=display_command,
        returncode=0,
        reason=TerminationReason.EXITED,
        stdout=stdout,
        stderr=b"",
        duration_s=0,
        truncated=False,
        cwd=".",
    )


if __name__ == "__main__":
    unittest.main()
