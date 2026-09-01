from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.interfaces.terminal_state import ApprovalBroker  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import ApprovalMode  # noqa: E402
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.errors import WindowsFileBusyError  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent_win.action_dispatcher import RootActionDispatcher  # noqa: E402


class FileBusyActionResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_returns_actionable_stable_file_busy_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "module.py"
            target.write_text("before\n", encoding="utf-8")
            guard = WorkspacePathGuard(root)
            editor = WorkspaceEditor(guard)
            dispatcher = RootActionDispatcher(
                WorkspaceFiles(guard, IgnoreRules.from_workspace(root)),
                editor,
                ActionPolicy(
                    PolicyConfig(ApprovalMode.AUTO, workspace_root=root)
                ),
                ApprovalBroker(),
            )
            blocked = WindowsFileBusyError(target, "edit file", 0.5, 5)

            with patch.object(editor, "apply", side_effect=blocked):
                result = await dispatcher.dispatch(
                    ActionRequest(
                        "busy",
                        "write_file",
                        {"path": "module.py", "content": "after\n"},
                    ),
                    CancellationToken(),
                )

        self.assertTrue(result.is_error)
        self.assertEqual(result.output["error_code"], "file_busy")
        self.assertEqual(result.output["error"], "Windows file operation blocked")
        self.assertIn("may be in use", result.output["detail"])
        self.assertIn("permissions or attributes", result.output["detail"])


if __name__ == "__main__":
    unittest.main()
