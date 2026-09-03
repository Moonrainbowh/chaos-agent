from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.interfaces.terminal_state import ApprovalBroker  # noqa: E402
from code_agent.policy.command_rules import ProcessRuleStore  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import ApprovalMode  # noqa: E402
from code_agent.runtime.models import CommandResult, TerminationReason  # noqa: E402
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent_win.app import RootActionDispatcher  # noqa: E402


class PermissionRuleIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rule_runs_in_ask_mode_and_pins_program(self) -> None:
        class Runtime:
            def __init__(self) -> None:
                self.argv = None

            async def run(self, spec, cancellation, sink):
                self.argv = spec.argv
                return CommandResult(
                    argv=spec.argv,
                    display_command="python -V",
                    returncode=0,
                    reason=TerminationReason.EXITED,
                    stdout=b"ok",
                    stderr=b"",
                    duration_s=0,
                    truncated=False,
                    cwd=".",
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runtime = Runtime()
            guard = WorkspacePathGuard(root)
            rules = ProcessRuleStore(root / "rules.sqlite3")
            fingerprint = "test-workspace"
            rule = rules.allow(
                sys.executable,
                ("-V",),
                workspace_root=root,
                workspace_fingerprint=fingerprint,
            )
            dispatcher = RootActionDispatcher(
                WorkspaceFiles(guard, IgnoreRules.from_workspace(root)),
                WorkspaceEditor(guard),
                ActionPolicy(PolicyConfig(ApprovalMode.ASK, workspace_root=root)),
                ApprovalBroker(),
                runtime=runtime,
                process_rules=rules,
                workspace_fingerprint=fingerprint,
            )

            result = await dispatcher.dispatch(
                ActionRequest(
                    "process-rule",
                    "run_process_v1",
                    {"program": sys.executable, "args": ["-V"], "cwd": "."},
                ),
                CancellationToken(),
            )

        self.assertFalse(result.is_error, result.output)
        self.assertEqual(runtime.argv[0], rule.program_path)
        self.assertEqual(result.metadata["permission_source"], "permanent_rule")
        self.assertEqual(result.metadata["permission_rule_id"], rule.id)


if __name__ == "__main__":
    unittest.main()
