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

from code_agent.workspace import _batch_apply, _windows_exact_move  # noqa: E402
from code_agent.workspace.edits import (  # noqa: E402
    BatchApplyStatus,
    CrossVolumeMoveError,
    WorkspaceEditor,
)
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows exact move semantics")
class WindowsBatchMoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.editor = WorkspaceEditor(WorkspacePathGuard(self.root))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_same_directory_move_preserves_bytes(self) -> None:
        source = self.root / "source.txt"
        source.write_bytes(b"payload")
        plan = self.editor.plan_batch(
            (self.editor.plan_move("source.txt", "destination.txt"),)
        )

        result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.APPLIED)
        self.assertFalse(source.exists())
        self.assertEqual((self.root / "destination.txt").read_bytes(), b"payload")

    def test_cross_directory_same_volume_move(self) -> None:
        source = self.root / "from" / "source.txt"
        destination = self.root / "to" / "destination.txt"
        source.parent.mkdir()
        destination.parent.mkdir()
        source.write_bytes(b"payload")
        before_inode = source.stat().st_ino
        plan = self.editor.plan_batch(
            (self.editor.plan_move("from/source.txt", "to/destination.txt"),)
        )

        result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.APPLIED)
        self.assertEqual(destination.read_bytes(), b"payload")
        self.assertEqual(destination.stat().st_ino, before_inode)

    def test_case_only_rename_records_alias_and_changes_exact_name(self) -> None:
        source = self.root / "MixedCase.txt"
        source.write_bytes(b"payload")
        move = self.editor.plan_move("MixedCase.txt", "MIXEDCASE.txt")

        self.assertFalse(move.destination.existed)
        self.assertTrue(move.destination.lookup_existed)
        self.assertTrue(move.destination.aliases_source)
        plan = self.editor.plan_batch((move,))
        result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.APPLIED)
        exact_names = {item.name for item in self.root.iterdir()}
        self.assertNotIn("MixedCase.txt", exact_names)
        self.assertIn("MIXEDCASE.txt", exact_names)
        self.assertEqual((self.root / "MIXEDCASE.txt").read_bytes(), b"payload")

    def test_cross_volume_rejected_before_native_rename(self) -> None:
        source = self.root / "source.txt"
        source.write_bytes(b"payload")
        plan = self.editor.plan_batch(
            (self.editor.plan_move("source.txt", "destination.txt"),)
        )

        with patch.object(
            _windows_exact_move, "_volume_ids", return_value=(10, 20)
        ), patch.object(_windows_exact_move, "_rename_no_replace") as rename:
            with self.assertRaises(CrossVolumeMoveError):
                self.editor.apply_batch(plan)

        rename.assert_not_called()
        self.assertEqual(source.read_bytes(), b"payload")
        self.assertFalse((self.root / "destination.txt").exists())

    def test_later_cross_volume_move_is_rejected_before_any_batch_write(self) -> None:
        source = self.root / "source.txt"
        source.write_bytes(b"payload")
        plan = self.editor.plan_batch(
            (
                self.editor.plan_write("created.txt", "created"),
                self.editor.plan_move("source.txt", "destination.txt"),
            )
        )

        with patch.object(
            _windows_exact_move, "_volume_ids", return_value=(10, 20)
        ), patch.object(
            _batch_apply,
            "write_bytes_exact",
            wraps=_batch_apply.write_bytes_exact,
        ) as write:
            with self.assertRaises(CrossVolumeMoveError):
                self.editor.apply_batch(plan)

        write.assert_not_called()
        self.assertFalse((self.root / "created.txt").exists())
        self.assertEqual(source.read_bytes(), b"payload")

    def test_destination_created_at_native_boundary_is_not_overwritten(self) -> None:
        source = self.root / "source.txt"
        destination = self.root / "destination.txt"
        source.write_bytes(b"source")
        plan = self.editor.plan_batch(
            (self.editor.plan_move("source.txt", "destination.txt"),)
        )
        real_rename = _windows_exact_move._rename_no_replace

        def create_then_rename(*args, **kwargs):
            destination.write_bytes(b"user")
            return real_rename(*args, **kwargs)

        with patch.object(
            _windows_exact_move, "_rename_no_replace", side_effect=create_then_rename
        ):
            result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual(source.read_bytes(), b"source")
        self.assertEqual(destination.read_bytes(), b"user")


if __name__ == "__main__":
    unittest.main()
