from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.runtime import _windows_directory  # noqa: E402
from code_agent.runtime._windows_directory import DirectoryLease  # noqa: E402
from code_agent.runtime.errors import RuntimeStartError  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows directory-handle behavior")
class WindowsDirectoryLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.guard = WorkspacePathGuard(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_open_lease_blocks_rename_until_handle_is_closed(self) -> None:
        target = self.root / "target"
        renamed = self.root / "renamed"
        target.mkdir()

        lease = DirectoryLease(target, self.guard)
        with lease as entered:
            self.assertIs(entered, lease)
            self.assertTrue(lease.active)
            self.assertEqual(lease.path, target)
            with self.assertRaises(OSError) as raised:
                os.rename(target, renamed)
            self.assertEqual(raised.exception.winerror, 32)

        self.assertFalse(lease.active)
        os.rename(target, renamed)
        self.assertTrue(renamed.is_dir())

    def test_rejects_different_final_path_and_closes_handle(self) -> None:
        target = self.root / "target"
        other = self.root / "other"
        renamed = self.root / "renamed"
        target.mkdir()
        other.mkdir()

        with patch(
            "code_agent.runtime._windows_directory._get_final_path_name",
            return_value="\\\\?\\" + str(other),
        ), patch(
            "code_agent.runtime._windows_directory._close_handle",
            wraps=_windows_directory._close_handle,
        ) as close_handle:
            with self.assertRaises(RuntimeStartError):
                with DirectoryLease(target, self.guard):
                    self.fail("a mismatched final path must not be leased")

        close_handle.assert_called_once()
        os.rename(target, renamed)
        self.assertTrue(renamed.is_dir())

    def test_rejects_final_path_outside_workspace_and_closes_handle(self) -> None:
        target = self.root / "target"
        renamed = self.root / "renamed"
        target.mkdir()

        with tempfile.TemporaryDirectory() as outside_temporary:
            outside = Path(outside_temporary).resolve()
            with patch(
                "code_agent.runtime._windows_directory._get_final_path_name",
                return_value="\\\\?\\" + str(outside),
            ), patch(
                "code_agent.runtime._windows_directory._close_handle",
                wraps=_windows_directory._close_handle,
            ) as close_handle:
                with self.assertRaises(RuntimeStartError):
                    with DirectoryLease(target, self.guard):
                        self.fail("an outside final path must not be leased")

        close_handle.assert_called_once()
        os.rename(target, renamed)
        self.assertTrue(renamed.is_dir())

    def test_missing_directory_is_reported_as_runtime_start_error(self) -> None:
        with self.assertRaises(RuntimeStartError) as raised:
            with DirectoryLease(self.root / "missing", self.guard):
                self.fail("a missing directory must not be leased")

        self.assertIsInstance(raised.exception.__cause__, OSError)


if __name__ == "__main__":
    unittest.main()
