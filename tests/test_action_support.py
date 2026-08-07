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

from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent_win.action_support import list_action_result  # noqa: E402


class _GitInventory:
    def __init__(self, paths: tuple[str, ...]) -> None:
        self.paths = paths
        self.calls = 0

    def snapshot_paths(self) -> tuple[str, ...]:
        self.calls += 1
        return self.paths


class ActionSupportTests(unittest.IsolatedAsyncioTestCase):
    async def test_workspace_root_uses_fast_git_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "a.py").write_text("a", encoding="utf-8")
            files = WorkspaceFiles(
                WorkspacePathGuard(root),
                IgnoreRules.from_workspace(root),
            )
            git = _GitInventory(("a.py",))

            with patch.object(
                files,
                "list_files",
                side_effect=AssertionError("recursive scan must not run"),
            ):
                result = await list_action_result(
                    ActionRequest("list-1", "list_files", {"root": "."}),
                    files,
                    git,  # type: ignore[arg-type]
                    ".",
                )

            self.assertEqual(git.calls, 1)
            self.assertEqual(result.output["files"], ("a.py",))
            self.assertFalse(result.output["truncated"])


if __name__ == "__main__":
    unittest.main()
