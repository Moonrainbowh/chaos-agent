from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent_win.app import RootActionDispatcher  # noqa: E402
from code_agent_win.tools import tool_definitions  # noqa: E402
from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.interfaces.terminal_state import ApprovalBroker  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import ApprovalMode  # noqa: E402
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class ToolSchemaTests(unittest.TestCase):
    def test_public_tools_have_strict_provider_compatible_object_schemas(self) -> None:
        definitions = {
            tool.name: tool.to_dict()["parameters"] for tool in tool_definitions()
        }

        self.assertEqual(
            set(definitions),
            {
                "read_file",
                "list_files",
                "search_text",
                "write_file",
                "replace_text",
                "git_status",
                "git_diff",
                "run_verification",
                "run_command",
                "delegate_agent",
            },
        )
        for parameters in definitions.values():
            self.assertEqual(parameters["type"], "object")
            self.assertFalse(parameters["additionalProperties"])
            self.assertIsInstance(parameters["required"], list)
            self.assertIsInstance(parameters["properties"], dict)

        self.assertEqual(definitions["read_file"]["required"], ["path"])
        self.assertEqual(definitions["read_file"]["properties"]["path"]["type"], "string")
        self.assertEqual(definitions["write_file"]["required"], ["path", "content"])
        self.assertEqual(definitions["replace_text"]["required"], ["path", "old_text", "new_text"])
        self.assertEqual(definitions["run_command"]["required"], ["command"])
        self.assertEqual(definitions["run_verification"]["required"], ["kind"])
        self.assertEqual(definitions["delegate_agent"]["required"], ["objective", "role"])
        self.assertEqual(definitions["git_diff"]["properties"]["paths"]["type"], "array")
        self.assertEqual(definitions["git_diff"]["properties"]["paths"]["items"]["type"], "string")
        self.assertEqual(definitions["git_diff"]["properties"]["paths"]["items"]["minLength"], 1)


class DispatcherValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "note.txt").write_text("before\n", encoding="utf-8")
        guard = WorkspacePathGuard(self.root)
        self.policy = Mock(wraps=ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=self.root)))
        self.files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        self.dispatcher = RootActionDispatcher(
            self.files,
            WorkspaceEditor(guard),
            self.policy,
            ApprovalBroker(),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_malformed_write_is_rejected_before_policy_or_file_side_effect(self) -> None:
        result = await self.dispatcher.dispatch(
            ActionRequest("call-1", "write_file", {"path": "note.txt", "content": ""}),
            CancellationToken(),
        )

        self.assertTrue(result.is_error)
        self.assertEqual(result.output["error"], "invalid tool arguments")
        self.policy.evaluate.assert_not_called()
        self.assertEqual((self.root / "note.txt").read_text(encoding="utf-8"), "before\n")

    async def test_unknown_property_is_rejected_before_runtime_side_effect(self) -> None:
        runtime = Mock()
        result = await RootActionDispatcher(
            self.files,
            WorkspaceEditor(WorkspacePathGuard(self.root)),
            self.policy,
            ApprovalBroker(),
            runtime=runtime,
        ).dispatch(
            ActionRequest("call-2", "run_command", {"command": "Get-Date", "unsafe": True}),
            CancellationToken(),
        )

        self.assertTrue(result.is_error)
        self.policy.evaluate.assert_not_called()
        runtime.run.assert_not_called()
