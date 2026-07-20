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

from code_agent.workspace import _guarded_read as guarded_read  # noqa: E402
from code_agent.workspace import _windows_guarded_open as windows_open  # noqa: E402
from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows handle semantics")
class WindowsOpenIntegrationTests(unittest.TestCase):
    def test_stable_leaf_missing_maps_to_guarded_missing(self) -> None:
        path = Path(r"C:\workspace\file.txt")
        missing = windows_open._WindowsLeafMissingError("stable leaf missing")

        with patch.object(
            guarded_read, "open_guarded_file", side_effect=missing
        ):
            with self.assertRaises(guarded_read._GuardedFileMissingError):
                guarded_read._open_existing(path, path.parent)


class DescriptorCloseSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.target = self.root / "file.bin"
        self.target.write_bytes(b"content")
        self.guard = WorkspacePathGuard(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_close_failure_does_not_mask_primary_read_error(self) -> None:
        real_close = os.close

        def close_then_fail(descriptor: int) -> None:
            real_close(descriptor)
            raise OSError("close failed")

        with (
            patch.object(
                guarded_read,
                "_read_bounded",
                side_effect=WorkspaceError("primary read failure"),
            ),
            patch.object(guarded_read.os, "close", side_effect=close_then_fail),
        ):
            with self.assertRaisesRegex(WorkspaceError, "primary read failure"):
                guarded_read.read_guarded_file("file.bin", self.guard, 1024)

    def test_close_failure_after_success_is_wrapped_stably(self) -> None:
        real_close = os.close

        def close_then_fail(descriptor: int) -> None:
            real_close(descriptor)
            raise OSError("close failed")

        with patch.object(guarded_read.os, "close", side_effect=close_then_fail):
            with self.assertRaisesRegex(
                WorkspaceError, "cannot close guarded file"
            ):
                guarded_read.read_guarded_file("file.bin", self.guard, 1024)


if __name__ == "__main__":
    unittest.main()
