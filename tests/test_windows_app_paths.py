from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.config.loader import load_runtime_config
from code_agent.workspace.errors import WindowsLongPathError
from code_agent.workspace.windows_paths import (
    LEGACY_SAFE_PATH_CHARS,
    windows_path_support,
)
from code_agent_win.app_paths import product_state_root
from code_agent_win.app import create_application
from code_agent_win.workspace_runtime import ManagedWorkspaceRuntime


@unittest.skipUnless(os.name == "nt", "Windows path limits are Windows-only")
class WindowsApplicationPathTests(unittest.TestCase):
    def setUp(self) -> None:
        windows_path_support.cache_clear()

    def tearDown(self) -> None:
        windows_path_support.cache_clear()

    def test_product_state_checks_descendants_before_creating_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = _directory_at_units(Path(temporary).resolve(), 126)
            product = base / "chaos-agent"
            self.assertEqual(_utf16_units(product), 138)

            with self._legacy_paths(), patch.dict(
                os.environ, {"LOCALAPPDATA": str(base)}, clear=False
            ):
                with self.assertRaisesRegex(
                    WindowsLongPathError, "application product state"
                ):
                    product_state_root()

            self.assertFalse(product.exists())

    def test_product_state_accepts_the_derived_path_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = _directory_at_units(Path(temporary).resolve(), 125)
            product = base / "chaos-agent"
            self.assertEqual(_utf16_units(product), 137)

            with self._legacy_paths(), patch.dict(
                os.environ, {"LOCALAPPDATA": str(base)}, clear=False
            ):
                actual = product_state_root()

            self.assertEqual(actual, product)
            self.assertTrue(product.is_dir())

    def test_product_state_rechecks_canonical_junction_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            canonical = _directory_at_units(base, 229)
            alias = _junction(base / "alias", canonical)

            with self._legacy_paths(), patch.dict(
                os.environ, {"LOCALAPPDATA": str(alias)}, clear=False
            ):
                with self.assertRaisesRegex(
                    WindowsLongPathError, "application product state"
                ):
                    product_state_root()

            self.assertFalse((canonical / "chaos-agent").exists())

    def test_managed_storage_rechecks_canonical_before_creating_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            parent = _directory_at_units(base, 234)
            canonical = parent / "storage"
            canonical.mkdir()
            self.assertEqual(_utf16_units(canonical), 242)
            alias = _junction(base / "alias", parent) / "storage"

            with self._legacy_paths():
                with self.assertRaisesRegex(
                    WindowsLongPathError, "managed workspace storage"
                ):
                    ManagedWorkspaceRuntime(object(), alias)

            self.assertFalse((canonical / "worktrees").exists())

    def test_managed_storage_checks_worktrees_before_creating_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = _directory_at_units(Path(temporary).resolve(), 223)
            storage = parent / "storage"
            self.assertEqual(_utf16_units(storage), 231)

            with self._legacy_paths():
                with self.assertRaisesRegex(
                    WindowsLongPathError, "managed workspace worktrees"
                ):
                    ManagedWorkspaceRuntime(object(), storage)

            self.assertFalse(storage.exists())

    def test_managed_storage_checks_snapshot_blobs_before_creating_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = _directory_at_units(Path(temporary).resolve(), 149)
            storage = parent / "storage"
            self.assertEqual(_utf16_units(storage), 157)

            with self._legacy_paths():
                with self.assertRaisesRegex(
                    WindowsLongPathError, "managed workspace snapshots"
                ):
                    ManagedWorkspaceRuntime(object(), storage)

            self.assertFalse(storage.exists())

    def test_product_path_failure_precedes_powershell_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runtime = _runtime_config(root)
            failure = WindowsLongPathError("application product state is too long")

            with patch(
                "code_agent_win.app._product_state_root", side_effect=failure
            ), patch(
                "code_agent_win.app._session_path",
                return_value=root / "state" / "sessions.sqlite3",
            ), patch(
                "code_agent_win.app.load_runtime_config", return_value=runtime
            ), patch(
                "code_agent_win.app.resolved_powershell_runtime"
            ) as resolve_powershell:
                with self.assertRaises(WindowsLongPathError):
                    create_application(root)

            resolve_powershell.assert_not_called()

    def test_storage_path_failure_precedes_powershell_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            state = root / "state"
            state.mkdir()
            runtime = _runtime_config(root)
            failure = WindowsLongPathError("workspace storage is too long")

            with patch(
                "code_agent_win.app._product_state_root", return_value=state
            ), patch(
                "code_agent_win.app._workspace_storage_path", side_effect=failure
            ), patch(
                "code_agent_win.app.load_runtime_config", return_value=runtime
            ), patch(
                "code_agent_win.app.resolved_powershell_runtime"
            ) as resolve_powershell:
                with self.assertRaises(WindowsLongPathError):
                    create_application(root)

            resolve_powershell.assert_not_called()

    def test_canonical_workspace_failure_precedes_powershell_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            parent = _directory_at_units(base, 234)
            canonical = parent / "workspace"
            canonical.mkdir()
            alias = _junction(base / "alias", parent) / "workspace"
            runtime = _runtime_config(base)

            with self._legacy_paths(), patch(
                "code_agent_win.app.load_runtime_config", return_value=runtime
            ), patch(
                "code_agent_win.app.resolved_powershell_runtime"
            ) as resolve_powershell:
                with self.assertRaisesRegex(WindowsLongPathError, "workspace root"):
                    create_application(alias)

            resolve_powershell.assert_not_called()

    @staticmethod
    def _legacy_paths():
        windows_path_support.cache_clear()
        return patch(
            "code_agent.workspace.windows_paths._read_long_paths_enabled",
            return_value=False,
        )


def _directory_at_units(base: Path, target_units: int) -> Path:
    current = base
    while _utf16_units(current) < target_units:
        remaining = target_units - _utf16_units(current) - 1
        current /= "d" * min(100, remaining)
        current.mkdir()
    return current


def _junction(link: Path, target: Path) -> Path:
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    if result.returncode:
        raise unittest.SkipTest("directory junctions are unavailable")
    return link


def _utf16_units(path: Path) -> int:
    return len(os.path.abspath(path).encode("utf-16-le")) // 2


def _runtime_config(root: Path):
    return load_runtime_config(env={
        "CHAOS_CONFIG": str(root / "missing.toml"),
        "CHAOS_API": "responses",
        "CHAOS_BASE_URL": "https://api.example.test",
        "CHAOS_MODEL": "test",
        "CHAOS_API_KEY_ENV": "KEY",
    })


if __name__ == "__main__":
    unittest.main()
