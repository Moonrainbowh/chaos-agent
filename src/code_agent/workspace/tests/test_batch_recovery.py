from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace import _batch_recovery  # noqa: E402
from code_agent.workspace.edits import (  # noqa: E402
    BatchApplyStatus,
    SnapshotEntry,
    WorkspaceSnapshot,
)
from code_agent.workspace.errors import SnapshotIntegrityError  # noqa: E402
from code_agent.workspace.tests._edit_test_support import (  # noqa: E402
    WorkspaceEditorTestCase,
)


class BatchRecoveryTests(WorkspaceEditorTestCase):
    def _prepared_recovery(self, operations):
        plan = self.editor.plan_batch(tuple(operations))
        prepared = self.editor.preflight_batch(plan)
        snapshot = _batch_recovery.snapshot_from_prepared(prepared)
        recovery = _batch_recovery.recovery_operations_from_prepared(prepared)
        return plan, snapshot, recovery

    @unittest.skipUnless(os.name == "nt", "Windows exact batch move semantics")
    def test_snapshot_contains_every_unique_preimage(self) -> None:
        (self.root / "update.txt").write_bytes(b"update-before")
        (self.root / "delete.txt").write_bytes(b"delete-before")
        (self.root / "move.txt").write_bytes(b"move-before")
        plan, snapshot, _ = self._prepared_recovery(
            (
                self.editor.plan_write("create.txt", "create-after"),
                self.editor.plan_write("update.txt", "update-after"),
                self.editor.plan_delete("delete.txt"),
                self.editor.plan_move("move.txt", "moved.txt"),
            )
        )

        self.assertEqual(len(plan.paths), 5)
        self.assertEqual(
            tuple(entry.relative_path for entry in snapshot.entries),
            ("create.txt", "update.txt", "delete.txt", "move.txt", "moved.txt"),
        )
        self.assertEqual(snapshot.entries[1].content, b"update-before")
        self.assertFalse(snapshot.entries[-1].existed)

    @unittest.skipUnless(os.name == "nt", "Windows case-only snapshot semantics")
    def test_case_only_snapshot_keeps_only_source_preimage(self) -> None:
        (self.root / "MixedCase.txt").write_bytes(b"before")
        _, snapshot, recovery = self._prepared_recovery(
            (self.editor.plan_move("MixedCase.txt", "MIXEDCASE.txt"),)
        )

        self.assertEqual(
            tuple(entry.relative_path for entry in snapshot.entries),
            ("MixedCase.txt",),
        )
        self.assertTrue(recovery[0].case_only)

    @unittest.skipUnless(os.name == "nt", "Windows exact batch move semantics")
    def test_recovery_needs_only_transitions_and_snapshot(self) -> None:
        first = self.root / "first.txt"
        deleted = self.root / "deleted.txt"
        moved = self.root / "moved.txt"
        first.write_bytes(b"first-before")
        deleted.write_bytes(b"deleted-before")
        moved.write_bytes(b"moved-before")
        plan, snapshot, recovery = self._prepared_recovery(
            (
                self.editor.plan_write("first.txt", "first-after"),
                self.editor.plan_delete("deleted.txt"),
                self.editor.plan_move("moved.txt", "destination.txt"),
            )
        )
        self.assertEqual(self.editor.apply_batch(plan).status, BatchApplyStatus.APPLIED)

        result = _batch_recovery.recover_batch(self.editor, recovery, snapshot)

        self.assertEqual(result.status, BatchApplyStatus.ROLLED_BACK)
        self.assertEqual(first.read_bytes(), b"first-before")
        self.assertEqual(deleted.read_bytes(), b"deleted-before")
        self.assertEqual(moved.read_bytes(), b"moved-before")
        self.assertFalse((self.root / "destination.txt").exists())

    def test_any_foreign_preflight_state_causes_zero_recovery_writes(self) -> None:
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes(b"first-before")
        second.write_bytes(b"second-before")
        plan, snapshot, recovery = self._prepared_recovery(
            (
                self.editor.plan_write("first.txt", "first-after"),
                self.editor.plan_write("second.txt", "second-after"),
            )
        )
        self.editor.apply_batch(plan)
        second.write_bytes(b"user-foreign")

        result = _batch_recovery.recover_batch(self.editor, recovery, snapshot)

        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual(first.read_bytes(), b"first-after")
        self.assertEqual(second.read_bytes(), b"user-foreign")
        self.assertEqual(result.rolled_back_operations, ())

    @unittest.skipUnless(os.name == "nt", "Windows exact batch move semantics")
    def test_corrupt_move_preimage_causes_zero_recovery_writes(self) -> None:
        source = self.root / "source.txt"
        destination = self.root / "destination.txt"
        source.write_bytes(b"source-before")
        plan, snapshot, recovery = self._prepared_recovery(
            (self.editor.plan_move("source.txt", "destination.txt"),)
        )
        self.assertEqual(self.editor.apply_batch(plan).status, BatchApplyStatus.APPLIED)
        corrupt = WorkspaceSnapshot(
            tuple(
                SnapshotEntry(entry.relative_path, b"corrupt", True)
                if entry.relative_path == "source.txt"
                else entry
                for entry in snapshot.entries
            )
        )

        with self.assertRaises(SnapshotIntegrityError):
            _batch_recovery.recover_batch(self.editor, recovery, corrupt)

        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(), b"source-before")

    @unittest.skipUnless(os.name == "nt", "Windows exact batch move semantics")
    def test_overlapping_recovery_journal_is_rejected_before_writes(self) -> None:
        source = self.root / "source.txt"
        destination = self.root / "destination.txt"
        source.write_bytes(b"source-before")
        plan, snapshot, recovery = self._prepared_recovery(
            (self.editor.plan_move("source.txt", "destination.txt"),)
        )
        self.assertEqual(self.editor.apply_batch(plan).status, BatchApplyStatus.APPLIED)

        with self.assertRaisesRegex(ValueError, "overlapping recovery path"):
            _batch_recovery.recover_batch(
                self.editor, recovery + recovery, snapshot
            )

        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(), b"source-before")

    def test_mid_recovery_drift_preserves_user_change(self) -> None:
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes(b"first-before")
        second.write_bytes(b"second-before")
        plan, snapshot, recovery = self._prepared_recovery(
            (
                self.editor.plan_write("first.txt", "first-after"),
                self.editor.plan_write("second.txt", "second-after"),
            )
        )
        self.editor.apply_batch(plan)
        real_recover = _batch_recovery._recover_operation
        calls = 0

        def drift_first(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                first.write_bytes(b"user-mid-recovery")
            return real_recover(*args, **kwargs)

        with patch.object(
            _batch_recovery, "_recover_operation", side_effect=drift_first
        ):
            result = _batch_recovery.recover_batch(self.editor, recovery, snapshot)

        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual(first.read_bytes(), b"user-mid-recovery")
        self.assertEqual(second.read_bytes(), b"second-before")
        self.assertEqual(result.rolled_back_operations, (1,))


if __name__ == "__main__":
    unittest.main()
