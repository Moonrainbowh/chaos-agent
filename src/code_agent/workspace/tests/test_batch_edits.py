from __future__ import annotations

import hashlib
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace import _batch_apply, _batch_plan  # noqa: E402
from code_agent.workspace.edits import (  # noqa: E402
    BatchApplyStatus,
    BatchEditConflictError,
)
from code_agent.workspace.tests._edit_test_support import (  # noqa: E402
    WorkspaceEditorTestCase,
)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class BatchPlanningTests(WorkspaceEditorTestCase):
    def test_plan_records_every_path_and_is_immutable(self) -> None:
        (self.root / "update.txt").write_bytes(b"before-update")
        (self.root / "delete.txt").write_bytes(b"before-delete")
        (self.root / "move.txt").write_bytes(b"before-move")

        operations = (
            self.editor.plan_write("create.txt", "created"),
            self.editor.plan_write("update.txt", "updated"),
            self.editor.plan_delete("delete.txt"),
            self.editor.plan_move("move.txt", "moved.txt"),
        )
        plan = self.editor.plan_batch(operations)
        states = {state.relative_path: state for state in plan.paths}

        self.assertEqual(len(plan.operations), 4)
        self.assertEqual(len(plan.plan_id), 64)
        self.assertEqual(plan.workspace_identity, self.editor.guard.root_identity)
        self.assertEqual(
            (states["update.txt"].existed, states["update.txt"].sha256,
             states["update.txt"].size),
            (True, _sha256(b"before-update"), len(b"before-update")),
        )
        self.assertEqual(
            (states["create.txt"].existed, states["create.txt"].sha256,
             states["create.txt"].size),
            (False, None, 0),
        )
        self.assertTrue(states["move.txt"].existed)
        self.assertFalse(states["moved.txt"].existed)
        with self.assertRaises(FrozenInstanceError):
            plan.operations = ()  # type: ignore[misc]

    def test_plan_id_is_deterministic_for_identical_operations(self) -> None:
        first = self.editor.plan_write("new.txt", "same")
        second = self.editor.plan_write("new.txt", "same")

        left = self.editor.plan_batch((first,))
        right = self.editor.plan_batch((second,))

        self.assertEqual(left.plan_id, right.plan_id)

    def test_batch_rejects_overlapping_paths(self) -> None:
        first = self.editor.plan_write("same.txt", "one")
        second = self.editor.plan_write("same.txt", "two")

        with self.assertRaisesRegex(ValueError, "overlapping batch path"):
            self.editor.plan_batch((first, second))

    def test_preflight_rejects_hash_valid_overlapping_plan(self) -> None:
        operation = self.editor.plan_write("same.txt", "after")
        valid = self.editor.plan_batch((operation,))
        operations = (operation, operation)
        paths = (valid.paths[0], valid.paths[0])
        forged = replace(
            valid,
            operations=operations,
            paths=paths,
            plan_id=_batch_plan._plan_id(
                valid.workspace_identity, operations, paths
            ),
        )

        with self.assertRaisesRegex(ValueError, "overlapping batch path"):
            self.editor.preflight_batch(forged)

    def test_move_requires_existing_source_and_missing_destination(self) -> None:
        (self.root / "source.txt").write_bytes(b"source")
        (self.root / "occupied.txt").write_bytes(b"occupied")

        with self.assertRaisesRegex(BatchEditConflictError, "source is missing"):
            self.editor.plan_move("missing.txt", "target.txt")
        with self.assertRaisesRegex(BatchEditConflictError, "destination exists"):
            self.editor.plan_move("source.txt", "occupied.txt")


class BatchApplyTests(WorkspaceEditorTestCase):
    def test_full_preflight_conflict_has_zero_writes(self) -> None:
        target = self.root / "update.txt"
        target.write_bytes(b"before")
        plan = self.editor.plan_batch(
            (
                self.editor.plan_write("created.txt", "created"),
                self.editor.plan_write("update.txt", "agent"),
            )
        )
        target.write_bytes(b"user")

        with self.assertRaises(BatchEditConflictError):
            self.editor.apply_batch(plan)

        self.assertFalse((self.root / "created.txt").exists())
        self.assertEqual(target.read_bytes(), b"user")

    def test_mid_batch_failure_rolls_back_prior_owned_write(self) -> None:
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes(b"first-before")
        second.write_bytes(b"second-before")
        plan = self.editor.plan_batch(
            (
                self.editor.plan_write("first.txt", "first-after"),
                self.editor.plan_write("second.txt", "second-after"),
            )
        )
        real_execute = _batch_apply._execute_operation
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second-operation failure")
            return real_execute(*args, **kwargs)

        with patch.object(_batch_apply, "_execute_operation", side_effect=fail_second):
            result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.ROLLED_BACK)
        self.assertEqual(first.read_bytes(), b"first-before")
        self.assertEqual(second.read_bytes(), b"second-before")
        self.assertEqual(result.rolled_back_operations, (0,))

    def test_foreign_postimage_is_preserved_as_partial_conflict(self) -> None:
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes(b"first-before")
        second.write_bytes(b"second-before")
        plan = self.editor.plan_batch(
            (
                self.editor.plan_write("first.txt", "first-after"),
                self.editor.plan_write("second.txt", "second-after"),
            )
        )
        real_execute = _batch_apply._execute_operation
        calls = 0

        def mutate_then_fail(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                first.write_bytes(b"user-foreign")
                raise OSError("injected failure after user mutation")
            return real_execute(*args, **kwargs)

        with patch.object(
            _batch_apply, "_execute_operation", side_effect=mutate_then_fail
        ):
            result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual(first.read_bytes(), b"user-foreign")
        self.assertEqual(second.read_bytes(), b"second-before")
        self.assertEqual(tuple(item.relative_path for item in result.conflicts),
                         ("first.txt",))

    def test_drift_between_steps_rolls_back_and_reports_conflict(self) -> None:
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes(b"first-before")
        second.write_bytes(b"second-before")
        plan = self.editor.plan_batch(
            (
                self.editor.plan_write("first.txt", "first-after"),
                self.editor.plan_write("second.txt", "second-after"),
            )
        )
        real_validate = _batch_apply._validate_all
        injected = False

        def mutate_after_first_postcondition(editor, expected):
            nonlocal injected
            real_validate(editor, expected)
            first_state = expected["first.txt"].state
            if not injected and first_state.sha256 == _sha256(b"first-after"):
                second.write_bytes(b"user-between-steps")
                injected = True

        with patch.object(
            _batch_apply, "_validate_all", side_effect=mutate_after_first_postcondition
        ):
            result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual(first.read_bytes(), b"first-before")
        self.assertEqual(second.read_bytes(), b"user-between-steps")
        self.assertEqual(
            tuple(item.relative_path for item in result.conflicts), ("second.txt",)
        )

    def test_delete_and_move_apply_together(self) -> None:
        deleted = self.root / "deleted.txt"
        source = self.root / "source.txt"
        destination = self.root / "nested" / "destination.txt"
        destination.parent.mkdir()
        deleted.write_bytes(b"delete")
        source.write_bytes(b"move")
        plan = self.editor.plan_batch(
            (
                self.editor.plan_delete("deleted.txt"),
                self.editor.plan_move("source.txt", "nested/destination.txt"),
            )
        )

        result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.APPLIED)
        self.assertFalse(deleted.exists())
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(), b"move")

    def test_failure_after_delete_and_move_restores_both(self) -> None:
        deleted = self.root / "deleted.txt"
        source = self.root / "source.txt"
        deleted.write_bytes(b"delete")
        source.write_bytes(b"move")
        plan = self.editor.plan_batch(
            (
                self.editor.plan_delete("deleted.txt"),
                self.editor.plan_move("source.txt", "destination.txt"),
                self.editor.plan_write("last.txt", "last"),
            )
        )
        real_execute = _batch_apply._execute_operation
        calls = 0

        def fail_last(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("injected final failure")
            return real_execute(*args, **kwargs)

        with patch.object(_batch_apply, "_execute_operation", side_effect=fail_last):
            result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.ROLLED_BACK)
        self.assertEqual(deleted.read_bytes(), b"delete")
        self.assertEqual(source.read_bytes(), b"move")
        self.assertFalse((self.root / "destination.txt").exists())
        self.assertFalse((self.root / "last.txt").exists())


if __name__ == "__main__":
    unittest.main()
