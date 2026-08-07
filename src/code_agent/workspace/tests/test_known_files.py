from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.errors import WorkspaceScanLimitError  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class KnownFilesTests(unittest.TestCase):
    def test_filters_missing_ignored_and_over_limit_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("a", encoding="utf-8")
            (root / "src" / "b.py").write_text("b", encoding="utf-8")
            (root / ".chaos-agent").mkdir()
            (root / ".chaos-agent" / "private").write_text(
                "private", encoding="utf-8"
            )
            files = WorkspaceFiles(
                WorkspacePathGuard(root),
                IgnoreRules.from_workspace(root),
            )

            listed = files.list_known_files(
                (
                    ".chaos-agent/private",
                    "missing.py",
                    "src/b.py",
                    "src/a.py",
                ),
                max_entries=1,
                max_scanned_entries=10,
            )

            self.assertEqual(listed, ("src/b.py",))

    def test_candidate_scan_budget_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            files = WorkspaceFiles(
                WorkspacePathGuard(root),
                IgnoreRules.from_workspace(root),
            )

            with self.assertRaises(WorkspaceScanLimitError):
                files.list_known_files(
                    ("missing-a", "missing-b"),
                    max_entries=1,
                    max_scanned_entries=1,
                )


if __name__ == "__main__":
    unittest.main()
