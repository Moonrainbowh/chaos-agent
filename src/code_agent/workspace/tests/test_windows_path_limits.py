from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.workspace.edits import SnapshotEntry, WorkspaceEditor, WorkspaceSnapshot
from code_agent.workspace.errors import WindowsLongPathError, WorkspaceError
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.snapshot_store import (
    ContentAddressedSnapshotStore,
    WorkspaceSnapshotStore,
)
from code_agent.workspace.windows_paths import (
    EXTENDED_SAFE_PATH_CHARS,
    LEGACY_SAFE_PATH_CHARS,
    require_supported_windows_path,
    windows_path_support,
)


@unittest.skipUnless(os.name == "nt", "Windows path limits are Windows-only")
class WindowsPathSupportTests(unittest.TestCase):
    def tearDown(self) -> None:
        windows_path_support.cache_clear()

    def test_disabled_policy_has_a_clear_legacy_budget(self) -> None:
        with patch(
            "code_agent.workspace.windows_paths._read_long_paths_enabled",
            return_value=False,
        ):
            status = windows_path_support()

        self.assertFalse(status.enabled)
        self.assertEqual(status.max_path_chars, LEGACY_SAFE_PATH_CHARS)
        self.assertIn("LongPathsEnabled=0", status.summary)

    def test_disabled_policy_rejects_before_filesystem_access(self) -> None:
        long_path = Path("C:/") / ("a" * LEGACY_SAFE_PATH_CHARS)
        with patch(
            "code_agent.workspace.windows_paths._read_long_paths_enabled",
            return_value=False,
        ):
            windows_path_support.cache_clear()
            with self.assertRaisesRegex(
                WindowsLongPathError, "Enable Win32 long paths"
            ):
                require_supported_windows_path(long_path, operation="workspace")

    def test_disabled_policy_counts_utf16_code_units(self) -> None:
        long_path = Path("C:/") / ("\U0001f600" * 119)
        self.assertLess(len(str(long_path)), LEGACY_SAFE_PATH_CHARS)
        self.assertGreater(_utf16_units(long_path), LEGACY_SAFE_PATH_CHARS)

        with patch(
            "code_agent.workspace.windows_paths._read_long_paths_enabled",
            return_value=False,
        ):
            windows_path_support.cache_clear()
            with self.assertRaisesRegex(
                WindowsLongPathError, "Enable Win32 long paths"
            ):
                require_supported_windows_path(long_path, operation="workspace")

    def test_enabled_policy_accepts_an_extended_length_path(self) -> None:
        long_path = Path("C:/") / ("a" * 300)
        with patch(
            "code_agent.workspace.windows_paths._read_long_paths_enabled",
            return_value=True,
        ):
            windows_path_support.cache_clear()
            status = windows_path_support()
            require_supported_windows_path(long_path, operation="workspace")

        self.assertTrue(status.enabled)
        self.assertEqual(status.max_path_chars, EXTENDED_SAFE_PATH_CHARS)

    def test_guard_reports_long_root_instead_of_missing_directory(self) -> None:
        long_root = Path("C:/") / ("r" * LEGACY_SAFE_PATH_CHARS)
        with patch(
            "code_agent.workspace.windows_paths._read_long_paths_enabled",
            return_value=False,
        ):
            windows_path_support.cache_clear()
            with self.assertRaises(WindowsLongPathError):
                WorkspacePathGuard(long_root)

    def test_apply_succeeds_at_the_legacy_safe_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = _boundary_target(root)
            editor = WorkspaceEditor(WorkspacePathGuard(root))

            plan = editor.plan_write(target.relative_to(root), "written\n")
            editor.apply(plan)

            self.assertEqual(target.read_text(encoding="utf-8"), "written\n")

    def test_restore_succeeds_at_the_legacy_safe_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = _boundary_target(root)
            target.write_bytes(b"before")
            editor = WorkspaceEditor(WorkspacePathGuard(root))
            snapshot = editor.snapshot((target.relative_to(root),))
            target.write_bytes(b"after")

            editor.restore(snapshot)

            self.assertEqual(target.read_bytes(), b"before")

    def test_rewind_store_rejects_unsafe_derived_paths_before_create(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            workspace = base / "workspace"
            workspace.mkdir()
            state = _child_at_units(base, 170, "s")

            with patch(
                "code_agent.workspace.windows_paths._read_long_paths_enabled",
                return_value=False,
            ):
                windows_path_support.cache_clear()
                with self.assertRaises(WindowsLongPathError):
                    WorkspaceSnapshotStore(WorkspacePathGuard(workspace), state)

            self.assertFalse(state.exists())

    def test_content_store_rejects_unsafe_derived_paths_at_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            state = _child_at_units(base, 167, "c")

            with patch(
                "code_agent.workspace.windows_paths._read_long_paths_enabled",
                return_value=False,
            ):
                windows_path_support.cache_clear()
                with self.assertRaises(WindowsLongPathError):
                    ContentAddressedSnapshotStore(state)

            self.assertFalse(state.exists())

    def test_content_store_rejects_unsafe_fallback_derived_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            fallback = _child_at_units(base, 167, "f")
            (fallback / "blobs").mkdir(parents=True)

            with patch(
                "code_agent.workspace.windows_paths._read_long_paths_enabled",
                return_value=False,
            ):
                windows_path_support.cache_clear()
                with self.assertRaises(WindowsLongPathError):
                    ContentAddressedSnapshotStore(
                        base / "primary",
                        read_fallback_roots=(fallback,),
                    )

    def test_rewind_store_can_round_trip_at_admitted_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            workspace = base / "workspace"
            workspace.mkdir()
            state = _child_at_units(base, 169, "r")
            store = WorkspaceSnapshotStore(WorkspacePathGuard(workspace), state)
            snapshot = WorkspaceSnapshot((SnapshotEntry("x", b"bytes", True),))

            handle = store.save(snapshot)

            self.assertEqual(store.load(handle), snapshot)

    def test_content_store_can_round_trip_at_admitted_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            state = _child_at_units(base, 166, "b")
            store = ContentAddressedSnapshotStore(state)
            snapshot = WorkspaceSnapshot((SnapshotEntry("x", b"bytes", True),))

            manifest = store.put(snapshot, {"x": 0o600})

            self.assertEqual(store.materialize(manifest).snapshot, snapshot)

    def test_artifact_path_metadata_errors_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            workspace = base / "workspace"
            workspace.mkdir()
            guard = WorkspacePathGuard(workspace)

            with patch.object(
                Path, "lstat", side_effect=PermissionError("denied")
            ):
                with self.assertRaisesRegex(
                    WorkspaceError, "inspect snapshot artifact"
                ):
                    WorkspaceSnapshotStore(guard, base / "state")


class LongPathScanFailureTests(unittest.TestCase):
    def test_inventory_does_not_silently_drop_a_long_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "visible.txt").write_bytes(b"visible")
            guard = WorkspacePathGuard(root)
            files = WorkspaceFiles(guard, IgnoreRules())
            original = guard.resolve

            def resolve(path: object, *, for_write: bool = False) -> Path:
                if Path(os.fspath(path)).name == "visible.txt":
                    raise WindowsLongPathError("path exceeds legacy limit")
                return original(path, for_write=for_write)

            with patch.object(guard, "resolve", side_effect=resolve):
                with self.assertRaises(WindowsLongPathError):
                    files.list_files()

    def test_subtree_scan_does_not_silently_drop_a_long_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            subtree = root / "top"
            subtree.mkdir()
            (subtree / "visible.txt").write_bytes(b"visible")
            guard = WorkspacePathGuard(root)
            files = WorkspaceFiles(guard, IgnoreRules())
            original = guard.resolve

            def resolve(path: object, *, for_write: bool = False) -> Path:
                if Path(os.fspath(path)).name == "visible.txt":
                    raise WindowsLongPathError("path exceeds legacy limit")
                return original(path, for_write=for_write)

            with patch.object(guard, "resolve", side_effect=resolve):
                with self.assertRaises(WindowsLongPathError):
                    files.list_files("top")


def _utf16_units(path: Path) -> int:
    absolute = os.path.abspath(path)
    return len(absolute.encode("utf-16-le")) // 2


def _boundary_target(root: Path) -> Path:
    parent = root
    parent_units = LEGACY_SAFE_PATH_CHARS - 2
    while _utf16_units(parent) < parent_units:
        remaining = parent_units - _utf16_units(parent) - 1
        component = "d" * min(100, remaining)
        parent /= component
        parent.mkdir()
    target = parent / "x"
    if _utf16_units(target) != LEGACY_SAFE_PATH_CHARS:
        raise AssertionError("test fixture did not reach the legacy boundary")
    return target


def _child_at_units(parent: Path, target_units: int, fill: str) -> Path:
    component_units = target_units - _utf16_units(parent) - 1
    if component_units <= 0 or component_units > 240:
        raise AssertionError("test fixture cannot build requested child path")
    child = parent / (fill * component_units)
    if _utf16_units(child) != target_units:
        raise AssertionError("test fixture did not reach requested path length")
    return child


if __name__ == "__main__":
    unittest.main()
