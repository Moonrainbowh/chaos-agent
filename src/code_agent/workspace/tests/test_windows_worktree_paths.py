from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.workspace.errors import WindowsLongPathError
from code_agent.workspace.windows_paths import (
    LEGACY_SAFE_PATH_CHARS,
    windows_path_support,
)
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.worktrees import WorktreeManager


@unittest.skipUnless(os.name == "nt", "Windows path limits are Windows-only")
class WindowsWorktreeEntryPathTests(unittest.TestCase):
    def tearDown(self) -> None:
        windows_path_support.cache_clear()

    def test_storage_root_is_checked_before_filesystem_access(self) -> None:
        long_root = Path("C:/") / ("s" * LEGACY_SAFE_PATH_CHARS)

        with patch(
            "code_agent.workspace.windows_paths._read_long_paths_enabled",
            return_value=False,
        ):
            windows_path_support.cache_clear()
            with self.assertRaisesRegex(
                WindowsLongPathError, "worktree storage root"
            ):
                WorktreeManager(long_root)

    def test_source_root_is_checked_before_strict_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            storage = Path(temporary).resolve()
            manager = WorktreeManager(storage)
            long_source = Path("C:/") / ("s" * LEGACY_SAFE_PATH_CHARS)

            with patch(
                "code_agent.workspace.windows_paths._read_long_paths_enabled",
                return_value=False,
            ):
                windows_path_support.cache_clear()
                with self.assertRaisesRegex(
                    WindowsLongPathError, "worktree source root"
                ):
                    manager.create(
                        long_source,
                        "lineage-1",
                        "codex/task-lineage-1",
                    )

    def test_workspace_root_rechecks_canonical_junction_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            literal = self._junction_to_long_root(Path(temporary).resolve())

            with patch(
                "code_agent.workspace.windows_paths._read_long_paths_enabled",
                return_value=False,
            ):
                windows_path_support.cache_clear()
                with self.assertRaisesRegex(
                    WindowsLongPathError, "workspace root"
                ):
                    WorkspacePathGuard(literal)

    def test_storage_root_rechecks_canonical_junction_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            literal = self._junction_to_long_root(Path(temporary).resolve())

            with patch(
                "code_agent.workspace.windows_paths._read_long_paths_enabled",
                return_value=False,
            ):
                windows_path_support.cache_clear()
                with self.assertRaisesRegex(
                    WindowsLongPathError, "worktree storage root"
                ):
                    WorktreeManager(literal)

    def test_source_root_rechecks_canonical_junction_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            storage = base / "storage"
            storage.mkdir()
            literal = self._junction_to_long_root(base)
            manager = WorktreeManager(storage)

            with patch(
                "code_agent.workspace.windows_paths._read_long_paths_enabled",
                return_value=False,
            ):
                windows_path_support.cache_clear()
                with patch.object(
                    manager,
                    "_git",
                    side_effect=AssertionError(
                        "canonical source limit was bypassed"
                    ),
                ):
                    with self.assertRaisesRegex(
                        WindowsLongPathError, "worktree source root"
                    ):
                        manager.create(
                            literal,
                            "lineage-1",
                            "codex/task-lineage-1",
                        )

    def _junction_to_long_root(self, base: Path) -> Path:
        parent = _directory_at_units(base, 234)
        canonical = parent / "subdir"
        canonical.mkdir()
        self.assertEqual(_utf16_units(canonical), 241)
        junction = base / "alias"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(parent)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        if result.returncode:
            self.skipTest("directory junctions are unavailable")
        literal = junction / "subdir"
        self.assertLess(_utf16_units(literal), LEGACY_SAFE_PATH_CHARS)
        return literal


def _directory_at_units(base: Path, target_units: int) -> Path:
    current = base
    while _utf16_units(current) < target_units:
        remaining = target_units - _utf16_units(current) - 1
        current /= "d" * min(100, remaining)
        current.mkdir()
    return current


def _utf16_units(path: Path) -> int:
    return len(os.path.abspath(path).encode("utf-16-le")) // 2


if __name__ == "__main__":
    unittest.main()
