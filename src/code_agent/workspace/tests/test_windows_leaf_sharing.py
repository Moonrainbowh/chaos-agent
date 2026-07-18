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

import code_agent.workspace._guarded_read as guarded_read  # noqa: E402
from code_agent.workspace import _windows_guarded_open as windows_open  # noqa: E402
from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows handle semantics")
class WindowsLeafSharingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.target = self.root / "large.bin"
        self.target.write_bytes(b"A" * (128 * 1024))
        self.guard = WorkspacePathGuard(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_leaf_create_uses_exact_read_share(self) -> None:
        with (
            patch.object(windows_open, "_create_handle", return_value=41) as create,
            patch.object(windows_open, "_handle_attributes", return_value=0x80),
            patch.object(windows_open, "_handle_to_descriptor", return_value=9),
        ):
            descriptor = windows_open._open_leaf(self.target)

        self.assertEqual(descriptor, 9)
        self.assertEqual(create.call_args.kwargs["share_mode"], 0x1)

    def test_existing_writer_causes_guarded_read_to_fail_closed(self) -> None:
        writer = windows_open._create_handle(
            self.target,
            desired_access=0x40000000,
            share_mode=0x7,
            flags=windows_open._OPEN_REPARSE_POINT,
        )
        try:
            with self.assertRaises(WorkspaceError):
                guarded_read.read_guarded_file(self.target, self.guard, 128 * 1024)
        finally:
            windows_open._close_handle(writer)

    def test_existing_delete_handle_causes_guarded_read_to_fail_closed(self) -> None:
        delete_handle = windows_open._create_handle(
            self.target,
            desired_access=0x10000,
            share_mode=0x7,
            flags=windows_open._OPEN_REPARSE_POINT,
        )
        try:
            with self.assertRaises(WorkspaceError):
                guarded_read.read_guarded_file(self.target, self.guard, 128 * 1024)
        finally:
            windows_open._close_handle(delete_handle)

    def test_guarded_read_blocks_future_write_delete_and_rename(self) -> None:
        attempted: list[str] = []

        def coordinated_read(descriptor: int, _max_bytes: int) -> bytes:
            first = os.read(descriptor, 64 * 1024)
            self._assert_mutations_blocked(attempted)
            return first + os.read(descriptor, 64 * 1024)

        with patch.object(guarded_read, "_read_bounded", side_effect=coordinated_read):
            content = guarded_read.read_guarded_file(
                self.target, self.guard, 128 * 1024
            )

        self.assertEqual(content, b"A" * (128 * 1024))
        self.assertEqual(attempted, ["write", "truncate", "delete", "rename"])

    def _assert_mutations_blocked(self, attempted: list[str]) -> None:
        with self.assertRaises(windows_open._WindowsOpenFailure):
            windows_open._create_handle(
                self.target,
                desired_access=0x40000000,
                share_mode=0x7,
                flags=windows_open._OPEN_REPARSE_POINT,
            )
        attempted.append("write")
        with self.assertRaises(PermissionError):
            self.target.open("wb")
        attempted.append("truncate")
        with self.assertRaises(PermissionError):
            self.target.unlink()
        attempted.append("delete")
        with self.assertRaises(PermissionError):
            self.target.rename(self.root / "moved.bin")
        attempted.append("rename")


if __name__ == "__main__":
    unittest.main()
