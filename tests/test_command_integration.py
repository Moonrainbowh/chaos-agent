from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.core.task import TaskAuthorization  # noqa: E402
from code_agent.interfaces.terminal_state import ApprovalBroker  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import ApprovalMode  # noqa: E402
from code_agent.runtime.models import (  # noqa: E402
    CommandResult,
    TerminationReason,
)
from code_agent.verification.python_adapter import PythonVerificationAdapter  # noqa: E402
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.git import GitWorkspace  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent_win.app import RootActionDispatcher  # noqa: E402
from code_agent_win.tool_support import (  # noqa: E402
    discover_git_workspace,
    windows_system_prompt,
)
from code_agent_win.tools import (  # noqa: E402
    powershell_compatibility_error,
    tool_definitions,
)
from code_agent_win.cli import _split_global_options  # noqa: E402
from code_agent.plugins.models import PluginRisk, ToolContribution  # noqa: E402
from code_agent.plugins.registry import (  # noqa: E402
    ContributionSnapshot,
    PluginHost,
    RegisteredContribution,
)
from code_agent_win.plugin_runtime import (  # noqa: E402
    PluginToolBridge,
)


def command_result(returncode: int = 0) -> CommandResult:
    return CommandResult(
        argv=("powershell",),
        display_command="<powershell-script>",
        returncode=returncode,
        reason=TerminationReason.EXITED,
        stdout=b"out",
        stderr=b"failure" if returncode else b"",
        duration_s=0,
        truncated=False,
        cwd=".",
    )


class CommandIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        guard = WorkspacePathGuard(self.root)
        self.files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        self.editor = WorkspaceEditor(guard)
        self.policy = Mock(
            wraps=ActionPolicy(
                PolicyConfig(ApprovalMode.AUTO, workspace_root=self.root)
            )
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def dispatcher(
        self, *, runtime: object | None = None, git: GitWorkspace | None = None
    ) -> RootActionDispatcher:
        return RootActionDispatcher(
            self.files,
            self.editor,
            self.policy,
            ApprovalBroker(),
            runtime=runtime,  # type: ignore[arg-type]
            git=git,
        )

    async def test_structured_verification_uses_fixed_argv(self) -> None:
        runtime = Mock()
        runtime.run = AsyncMock(return_value=command_result())
        dispatcher = RootActionDispatcher(self.files, self.editor, self.policy, ApprovalBroker(), runtime=runtime, verification=PythonVerificationAdapter(self.root))

        result = await dispatcher.dispatch(ActionRequest("verify", "run_verification", {"kind": "python_compileall", "targets": ["src"]}), CancellationToken(), TaskAuthorization.local_workspace(str(self.root)))

        self.assertFalse(result.is_error)
        spec = runtime.run.await_args.args[0]
        self.assertEqual(spec.argv[1:3], ("-m", "compileall"))
        self.assertIsNone(spec.powershell_script)

    async def test_mcp_read_tool_flows_through_policy_then_controller(self) -> None:
        class Mcp:
            def definitions(self): return ()
            async def call(self, name: str, arguments: object):
                self.seen = (name, arguments)
                return {"ok": True}
        mcp = Mcp()
        self.policy._mock_wraps.config.mcp_risks["mcp.docs.search"] = "read"
        dispatcher = RootActionDispatcher(self.files, self.editor, self.policy, ApprovalBroker(), mcp=mcp)

        result = await dispatcher.dispatch(ActionRequest("mcp-1", "mcp.docs.search", {"query": "policy"}), CancellationToken())

        self.assertFalse(result.is_error)
        self.assertEqual(mcp.seen[0], "mcp.docs.search")
        self.policy.evaluate.assert_called_once()

    async def test_plugin_tool_must_pass_plugin_and_host_target_policy(self) -> None:
        tool = ToolContribution(
            "save",
            "Save through a host action.",
            "write_file",
            PluginRisk.READ,
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        )
        registered = RegisteredContribution("plugin-a", "plug", "tool", "save", tool)
        bridge = PluginToolBridge(PluginHost(ContributionSnapshot(contributions=(registered,))))
        policy = Mock(
            wraps=ActionPolicy(
                PolicyConfig(
                    ApprovalMode.ASK,
                    workspace_root=self.root,
                    mcp_risks={"plug.save": "read"},
                )
            )
        )
        dispatcher = RootActionDispatcher(
            self.files, self.editor, policy, ApprovalBroker(), plugins=bridge
        )

        result = await dispatcher.dispatch(
            ActionRequest(
                "plugin-1",
                "plug.save",
                {"path": "note.txt", "content": "unsafe"},
            ),
            CancellationToken(),
        )

        self.assertTrue(result.is_error)
        self.assertEqual(result.output["error_code"], "approval_required")
        self.assertEqual(policy.evaluate.call_count, 2)
        self.assertFalse((self.root / "note.txt").exists())

    async def test_bash_here_string_is_rejected_before_policy_and_runtime(self) -> None:
        runtime = Mock()
        runtime.run = AsyncMock()

        result = await self.dispatcher(runtime=runtime).dispatch(
            ActionRequest(
                "call-1",
                "run_command",
                {"command": 'python addition.py <<< "3\n4"'},
            ),
            CancellationToken(),
            TaskAuthorization.local_workspace(str(self.root)),
        )

        self.assertTrue(result.is_error)
        self.assertEqual(result.output["error"], "shell syntax mismatch")
        self.assertIn("PowerShell", result.output["detail"])
        self.policy.evaluate.assert_not_called()
        runtime.run.assert_not_awaited()

    async def test_local_raw_shell_runs_under_current_workspace_trust(self) -> None:
        runtime = Mock()
        runtime.run = AsyncMock(return_value=command_result())

        result = await self.dispatcher(runtime=runtime).dispatch(
            ActionRequest(
                "call-2",
                "run_command",
                {"command": 'python -c "print(\'<<<\')"'},
            ),
            CancellationToken(),
            TaskAuthorization.local_workspace(str(self.root)),
        )

        self.assertFalse(result.is_error)
        runtime.run.assert_awaited_once()

    async def test_local_raw_shell_reports_runtime_failure_without_approval(self) -> None:
        runtime = Mock()
        runtime.run = AsyncMock(return_value=command_result(7))

        result = await self.dispatcher(runtime=runtime).dispatch(
            ActionRequest(
                "call-3",
                "run_command",
                {"command": 'python -c "raise SystemExit(7)"'},
            ),
            CancellationToken(),
            TaskAuthorization.local_workspace(str(self.root)),
        )

        self.assertTrue(result.is_error)
        self.assertEqual(result.output["error"], "command failed")
        self.assertEqual(result.output["returncode"], 7)
        runtime.run.assert_awaited_once()

    async def test_git_error_keeps_the_actionable_reason(self) -> None:
        result = await self.dispatcher(git=GitWorkspace(self.root)).dispatch(
            ActionRequest("call-4", "git_status", {}),
            CancellationToken(),
        )

        self.assertTrue(result.is_error)
        self.assertEqual(result.output["error"], "git command failed")
        self.assertIn("not a git repository", result.output["detail"].lower())

    def test_dispatcher_advertises_git_only_when_adapter_is_present(self) -> None:
        without_git = {tool.name for tool in self.dispatcher().tools()}
        with_git = {
            tool.name for tool in self.dispatcher(git=GitWorkspace(self.root)).tools()
        }

        self.assertNotIn("git_status", without_git)
        self.assertNotIn("git_diff", without_git)
        self.assertIn("git_status", with_git)
        self.assertIn("git_diff", with_git)


class CapabilityTests(unittest.TestCase):
    def test_global_profile_options_are_order_independent(self) -> None:
        profile, model, command = _split_global_options(("ask", "question", "--model", "fast", "--profile", "company"))
        self.assertEqual((profile, model, command), ("company", "fast", ("ask", "question")))
    def test_tool_schema_can_omit_git_without_changing_other_tools(self) -> None:
        names = {tool.name for tool in tool_definitions(include_git=False)}

        self.assertNotIn("git_status", names)
        self.assertNotIn("git_diff", names)
        self.assertIn("run_command", names)

    def test_powershell_contract_and_literal_detection_are_explicit(self) -> None:
        description = next(
            tool.description for tool in tool_definitions() if tool.name == "run_command"
        )

        self.assertIn("PowerShell", description)
        self.assertIn("<<<", description)
        self.assertIsNotNone(powershell_compatibility_error("python x.py <<< '1'"))
        self.assertIsNone(powershell_compatibility_error("Write-Output '<<<'"))

    def test_git_discovery_distinguishes_plain_directory_and_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.assertIsNone(discover_git_workspace(root))
            subprocess.run(
                ["git", "init", "-q"],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            self.assertIsNotNone(discover_git_workspace(root))

    def test_system_prompt_declares_shell_and_git_capability(self) -> None:
        plain = windows_system_prompt(False)
        repository = windows_system_prompt(True)

        self.assertIn("PowerShell runtime information is unavailable", plain)
        self.assertIn("run_process_v1", plain)
        self.assertIn("readable Markdown", plain)
        self.assertIn("**bold emphasis**", plain)
        self.assertIn("not a Git repository", plain)
        self.assertIn("Git repository", repository)


if __name__ == "__main__":
    unittest.main()
