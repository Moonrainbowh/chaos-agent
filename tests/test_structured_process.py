from __future__ import annotations

import json
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
from code_agent.interfaces.terminal_state import ApprovalBroker  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import ApprovalMode  # noqa: E402
from code_agent.runtime.local import WindowsLocalRuntime  # noqa: E402
from code_agent.runtime.models import (  # noqa: E402
    CommandResult,
    CommandSpec,
    TerminationReason,
)
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent_win.action_dispatcher import RootActionDispatcher  # noqa: E402


class RecordingRuntime:
    def __init__(self) -> None:
        self.run = AsyncMock(side_effect=self._run)

    async def _run(
        self, spec: CommandSpec, cancellation: CancellationToken, sink: object
    ) -> CommandResult:
        return CommandResult(
            argv=spec.argv or ("missing",),
            display_command="<structured-process>",
            returncode=0,
            reason=TerminationReason.EXITED,
            stdout=b"ok",
            stderr=b"",
            duration_s=0,
            truncated=False,
            cwd=spec.cwd.as_posix(),
        )


class StructuredProcessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "nested").mkdir()
        guard = WorkspacePathGuard(self.root)
        self.files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        self.editor = WorkspaceEditor(guard)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def dispatcher(
        self, runtime: object, policy: object | None = None
    ) -> RootActionDispatcher:
        selected = policy or ActionPolicy(
            PolicyConfig(ApprovalMode.UNRESTRICTED, workspace_root=self.root)
        )
        return RootActionDispatcher(
            self.files,
            self.editor,
            selected,  # type: ignore[arg-type]
            ApprovalBroker(),
            runtime=runtime,  # type: ignore[arg-type]
        )

    async def test_exact_argv_cwd_and_timeout_reach_runtime_without_shell(self) -> None:
        runtime = RecordingRuntime()
        arguments = {
            "program": "tool.exe",
            "args": ["", "two words", "a&b|c;d"],
            "cwd": "nested",
            "timeout_s": 17,
        }

        result = await self.dispatcher(runtime).dispatch(
            ActionRequest("process", "run_process_v1", arguments),
            CancellationToken(),
        )

        self.assertFalse(result.is_error)
        spec = runtime.run.await_args.args[0]
        self.assertEqual(spec.argv, ("tool.exe", "", "two words", "a&b|c;d"))
        self.assertEqual(spec.cwd, Path("nested"))
        self.assertEqual(spec.timeout_s, 17)
        self.assertIsNone(spec.powershell_script)
        self.assertIsNone(spec.shell_script)
        self.assertEqual(dict(spec.explicit_env), {})

    async def test_contract_rejections_happen_before_policy_and_runtime(self) -> None:
        runtime = RecordingRuntime()
        policy = Mock(
            wraps=ActionPolicy(
                PolicyConfig(ApprovalMode.UNRESTRICTED, workspace_root=self.root)
            )
        )
        invalid = (
            ({"program": "tool.exe"}, "invalid tool arguments"),
            ({"program": "pwsh.exe", "args": []}, "process contract mismatch"),
            ({"program": "build.CMD", "args": []}, "process contract mismatch"),
            ({"program": "build.bat", "args": []}, "process contract mismatch"),
            ({"program": "tool\x00.exe", "args": []}, "process contract mismatch"),
            ({"program": "tool.exe", "args": ["bad\x00arg"]}, "process contract mismatch"),
            ({"program": "tool.exe", "args": [], "timeout_s": True}, "invalid tool arguments"),
            ({"program": "tool.exe", "args": [], "env": {}}, "invalid tool arguments"),
            ({"program": "tool.exe", "args": ["x"] * 129}, "invalid tool arguments"),
            ({"program": "tool.exe", "args": ["x" * 8000] * 4}, "process contract mismatch"),
        )

        for index, (arguments, expected) in enumerate(invalid):
            with self.subTest(arguments=arguments):
                result = await self.dispatcher(runtime, policy).dispatch(
                    ActionRequest(str(index), "run_process_v1", arguments),
                    CancellationToken(),
                )
                self.assertTrue(result.is_error)
                self.assertEqual(result.output["error"], expected)

        policy.evaluate.assert_not_called()
        runtime.run.assert_not_awaited()

    async def test_literal_metacharacters_survive_real_windows_process(self) -> None:
        runtime = WindowsLocalRuntime(self.root)
        literal = "two words & | ; $() %PATH%"
        script = "import json,sys; print(json.dumps(sys.argv[1:]))"

        result = await self.dispatcher(runtime).dispatch(
            ActionRequest(
                "literal",
                "run_process_v1",
                {"program": sys.executable, "args": ["-c", script, literal]},
            ),
            CancellationToken(),
        )

        self.assertFalse(result.is_error, result.output)
        self.assertEqual(json.loads(result.output["stdout"]), [literal])


if __name__ == "__main__":
    unittest.main()
