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

import code_agent_win.workspace_context as workspace_context  # noqa: E402
from code_agent_win.workspace_context import workspace_uses_repo_map  # noqa: E402


class WorkspaceContextModeTests(unittest.TestCase):
    def test_git_workspace_uses_repo_map_without_marker_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            self.assertTrue(workspace_uses_repo_map(root, git_available=True))

    def test_direct_project_marker_enables_repo_map(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

            self.assertTrue(workspace_uses_repo_map(root, git_available=False))

    def test_nested_marker_does_not_turn_a_broad_directory_into_a_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nested = root / "downloads" / "demo"
            nested.mkdir(parents=True)
            (nested / "package.json").write_text("{}", encoding="utf-8")

            self.assertFalse(workspace_uses_repo_map(root, git_available=False))

    def test_home_and_filesystem_root_stay_lightweight_even_with_git(self) -> None:
        filesystem_root = Path(Path.cwd().anchor)
        for root in (Path.home(), filesystem_root):
            for git_available in (False, True):
                with self.subTest(root=root, git_available=git_available):
                    self.assertFalse(
                        workspace_uses_repo_map(
                            root, git_available=git_available
                        )
                    )

    def test_suffix_marker_scan_has_a_hard_direct_entry_limit(self) -> None:
        class Entry:
            def __init__(self, index: int) -> None:
                self.name = f"entry-{index}.txt"

            def is_symlink(self) -> bool:
                return False

            def is_file(self, *, follow_symlinks: bool = True) -> bool:
                return True

        class Scandir:
            def __init__(self) -> None:
                self.consumed = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def __iter__(self):
                return self

            def __next__(self):
                self.consumed += 1
                return Entry(self.consumed)

        with tempfile.TemporaryDirectory() as temporary:
            scan = Scandir()
            with patch.object(
                workspace_context.os, "scandir", return_value=scan
            ):
                enabled = workspace_uses_repo_map(
                    Path(temporary), git_available=False
                )

        self.assertFalse(enabled)
        self.assertLessEqual(
            scan.consumed, workspace_context.MAX_PROJECT_MARKER_SCAN
        )


if __name__ == "__main__":
    unittest.main()
