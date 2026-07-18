from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace import _windows_guarded_open as windows_open  # noqa: E402
from code_agent.workspace.errors import WorkspaceError  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows handle semantics")
class WindowsDescriptorOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(r"C:\workspace")
        self.target = self.root / "file.bin"

    def test_exception_during_transfer_releases_native_handle(self) -> None:
        with (
            patch.object(windows_open, "_create_handle", return_value=41),
            patch.object(windows_open, "_handle_attributes", return_value=0x80),
            patch.object(
                windows_open,
                "_handle_to_descriptor",
                side_effect=OSError("transfer failed"),
            ),
            patch.object(windows_open, "_close_handle") as close_handle,
        ):
            with self.assertRaises(WorkspaceError):
                windows_open._open_leaf(self.target)

        close_handle.assert_called_once_with(41)

    def test_base_exception_during_transfer_does_not_reclose_native_handle(
        self,
    ) -> None:
        primary = KeyboardInterrupt("ownership may have transferred")
        with (
            patch.object(windows_open, "_create_handle", return_value=41),
            patch.object(windows_open, "_handle_attributes", return_value=0x80),
            patch.object(
                windows_open, "_handle_to_descriptor", side_effect=primary
            ),
            patch.object(windows_open, "_close_handle") as close_handle,
        ):
            with self.assertRaises(KeyboardInterrupt) as captured:
                windows_open._open_leaf(self.target)

        self.assertIs(captured.exception, primary)
        close_handle.assert_not_called()

    def test_post_transfer_base_exception_closes_descriptor_and_all_parents(
        self,
    ) -> None:
        parents = (Path("C:/"), self.root)
        primary = KeyboardInterrupt("after descriptor transfer")
        closed: list[int] = []
        real_close_all = windows_open._close_all
        close_attempts = 0

        def interrupt_then_close(handles: list[int]):
            nonlocal close_attempts
            close_attempts += 1
            if close_attempts == 1:
                raise primary
            return real_close_all(handles)

        with (
            patch.object(windows_open, "_parent_paths", return_value=parents),
            patch.object(windows_open, "_open_parent", side_effect=(11, 12)),
            patch.object(windows_open, "_open_leaf", return_value=9),
            patch.object(
                windows_open, "_close_all", side_effect=interrupt_then_close
            ),
            patch.object(
                windows_open,
                "_close_handle",
                side_effect=lambda handle: closed.append(handle),
            ),
            patch.object(windows_open.os, "close") as close_descriptor,
        ):
            with self.assertRaises(KeyboardInterrupt) as captured:
                windows_open.open_guarded_file(self.target, self.root)

        self.assertIs(captured.exception, primary)
        close_descriptor.assert_called_once_with(9)
        self.assertEqual(closed, [12, 11])

    def test_parent_close_base_exception_retries_owned_handle_during_cleanup(
        self,
    ) -> None:
        primary = KeyboardInterrupt("during parent close")
        attempts: list[int] = []

        def interrupt_once(handle: int) -> None:
            attempts.append(handle)
            if attempts == [12]:
                raise primary

        with (
            patch.object(
                windows_open, "_parent_paths", return_value=(Path("C:/"), self.root)
            ),
            patch.object(windows_open, "_open_parent", side_effect=(11, 12)),
            patch.object(windows_open, "_open_leaf", return_value=9),
            patch.object(windows_open, "_close_handle", side_effect=interrupt_once),
            patch.object(windows_open.os, "close") as close_descriptor,
        ):
            with self.assertRaises(KeyboardInterrupt) as captured:
                windows_open.open_guarded_file(self.target, self.root)

        self.assertIs(captured.exception, primary)
        close_descriptor.assert_called_once_with(9)
        self.assertEqual(attempts, [12, 12, 11])


if __name__ == "__main__":
    unittest.main()
