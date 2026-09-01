from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace import _batch_apply, _batch_plan, _batch_recovery  # noqa: E402
from code_agent.workspace.edits import BatchApplyStatus  # noqa: E402
from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace.tests._edit_test_support import (  # noqa: E402
    WorkspaceEditorTestCase,
)


class BatchIdentityHardeningTests(WorkspaceEditorTestCase):
    def test_same_bytes_new_inode_between_validation_and_write_is_conflict(self) -> None:
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

        def replace_second_then_execute(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                replacement = self.root / "replacement.tmp"
                replacement.write_bytes(b"second-before")
                os.replace(replacement, second)
            return real_execute(*args, **kwargs)

        with patch.object(
            _batch_apply,
            "_execute_operation",
            side_effect=replace_second_then_execute,
        ):
            result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual(first.read_bytes(), b"first-before")
        self.assertEqual(second.read_bytes(), b"second-before")
        self.assertEqual(result.rolled_back_operations, (0,))

    def test_same_inode_same_bytes_with_new_mtime_is_conflict(self) -> None:
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

        def touch_second_then_execute(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                metadata = second.stat()
                os.utime(
                    second,
                    ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 2_000_000_000),
                )
            return real_execute(*args, **kwargs)

        with patch.object(
            _batch_apply,
            "_execute_operation",
            side_effect=touch_second_then_execute,
        ):
            result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual(first.read_bytes(), b"first-before")
        self.assertEqual(second.read_bytes(), b"second-before")
        self.assertEqual(result.rolled_back_operations, (0,))

    def test_classification_observe_error_still_rolls_back_prior_owned_edit(self) -> None:
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
        real_observe = _batch_apply.observe
        calls = 0
        fail_observe = False

        def fail_second(*args, **kwargs):
            nonlocal calls, fail_observe
            calls += 1
            if calls == 2:
                fail_observe = True
                raise OSError("injected operation failure")
            return real_execute(*args, **kwargs)

        def observe_once(editor, relative):
            nonlocal fail_observe
            if fail_observe and relative == "second.txt":
                fail_observe = False
                raise OSError("injected classification observation failure")
            return real_observe(editor, relative)

        with patch.object(_batch_apply, "_execute_operation", side_effect=fail_second), patch.object(
            _batch_apply, "observe", side_effect=observe_once
        ):
            result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual(first.read_bytes(), b"first-before")
        self.assertEqual(second.read_bytes(), b"second-before")
        self.assertEqual(result.rolled_back_operations, (0,))

    def test_rollback_observe_error_does_not_skip_other_owned_edit(self) -> None:
        paths = [self.root / name for name in ("first.txt", "second.txt", "third.txt")]
        for index, path in enumerate(paths):
            path.write_bytes(f"before-{index}".encode())
        plan = self.editor.plan_batch(
            tuple(
                self.editor.plan_write(path.name, f"after-{index}")
                for index, path in enumerate(paths)
            )
        )
        real_execute = _batch_apply._execute_operation
        from code_agent.workspace import _batch_rollback

        real_observe = _batch_rollback.observe
        calls = 0
        rollback_started = False

        def fail_third(*args, **kwargs):
            nonlocal calls, rollback_started
            calls += 1
            if calls == 3:
                rollback_started = True
                raise OSError("injected final operation failure")
            return real_execute(*args, **kwargs)

        def fail_second_rollback_observe(editor, relative):
            nonlocal rollback_started
            if rollback_started and relative == "second.txt":
                rollback_started = False
                raise OSError("injected rollback observation failure")
            return real_observe(editor, relative)

        with patch.object(_batch_apply, "_execute_operation", side_effect=fail_third), patch.object(
            _batch_rollback, "observe", side_effect=fail_second_rollback_observe
        ):
            result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual(paths[0].read_bytes(), b"before-0")
        self.assertEqual(paths[1].read_bytes(), b"after-1")
        self.assertEqual(result.rolled_back_operations, (0,))

    def test_rollback_preserves_same_inode_postimage_with_new_mtime(self) -> None:
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

        def touch_owned_then_fail(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                metadata = first.stat()
                os.utime(
                    first,
                    ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 2_000_000_000),
                )
                raise OSError("injected second-operation failure")
            return real_execute(*args, **kwargs)

        with patch.object(
            _batch_apply,
            "_execute_operation",
            side_effect=touch_owned_then_fail,
        ):
            result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual(first.read_bytes(), b"first-after")
        self.assertEqual(second.read_bytes(), b"second-before")
        self.assertEqual(result.rolled_back_operations, ())

    def test_owned_postimage_requires_full_identity_not_only_inode(self) -> None:
        from code_agent.workspace import _batch_observe, _batch_rollback

        target = self.root / "owned.txt"
        target.write_bytes(b"same")
        owned = _batch_observe.observe(self.editor, "owned.txt")
        metadata = target.stat()
        os.utime(
            target,
            ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 2_000_000_000),
        )
        current = _batch_observe.observe(self.editor, "owned.txt")

        conflicts = _batch_rollback.owned_mismatches((current,), (owned,))

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].relative_path, "owned.txt")


@unittest.skipUnless(os.name == "nt", "Windows batch path semantics")
class WindowsBatchHardeningTests(WorkspaceEditorTestCase):
    def test_delete_recovery_preserves_new_case_alias(self) -> None:
        source = self.root / "MixedCase.txt"
        alias = self.root / "MIXEDCASE.TXT"
        source.write_bytes(b"before")
        plan = self.editor.plan_batch((self.editor.plan_delete("MixedCase.txt"),))
        prepared = self.editor.preflight_batch(plan)
        snapshot = _batch_recovery.snapshot_from_prepared(prepared)
        recovery = _batch_recovery.recovery_operations_from_prepared(prepared)
        self.assertEqual(self.editor.apply_batch(plan).status, BatchApplyStatus.APPLIED)
        alias.write_bytes(b"user")

        result = self.editor.recover_batch(recovery, snapshot)

        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual(alias.read_bytes(), b"user")
        self.assertFalse(source.exists() and source.name in {item.name for item in self.root.iterdir()})
        self.assertEqual(result.rolled_back_operations, ())

    def test_case_sensitive_move_is_rejected_before_alias_planning(self) -> None:
        (self.root / "File.txt").write_bytes(b"payload")

        with patch.object(
            _batch_plan,
            "_directory_is_case_sensitive",
            return_value=True,
            create=True,
        ):
            with self.assertRaisesRegex(WorkspaceError, "case-sensitive"):
                self.editor.plan_move("File.txt", "file.txt")

    def test_case_sensitive_directory_allows_ordinary_different_name_move(self) -> None:
        (self.root / "File.txt").write_bytes(b"payload")

        with patch.object(
            _batch_plan,
            "_directory_is_case_sensitive",
            return_value=True,
        ) as inspect_case:
            move = self.editor.plan_move("File.txt", "other.txt")

        inspect_case.assert_not_called()
        self.assertFalse(move.case_only)
        self.assertEqual(move.destination.relative_path, "other.txt")


if __name__ == "__main__":
    unittest.main()
