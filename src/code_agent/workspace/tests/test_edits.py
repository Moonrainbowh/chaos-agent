from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.edits import (  # noqa: E402
    SnapshotEntry,
    WorkspaceEditor,
    WorkspaceSnapshot,
    build_restore_snapshot,
)
from code_agent.workspace.errors import (  # noqa: E402
    EditConflictError,
    FileTooLargeError,
    WorkspaceError,
)
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent.workspace.tests._edit_test_support import (  # noqa: E402
    WorkspaceEditorTestCase,
)

class EditPlanningTests(WorkspaceEditorTestCase):
    def test_editor_configuration_bounds_existing_file_reads(self) -> None:
        target = self.root / "large.txt"
        target.write_bytes(b"1234")

        self.assertGreater(self.editor.max_file_bytes, 0)
        for invalid in (0, -1, True, 1.5):
            with self.subTest(max_file_bytes=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    WorkspaceEditor(
                        WorkspacePathGuard(self.root),
                        max_file_bytes=invalid,  # type: ignore[arg-type]
                    )

        editor = WorkspaceEditor(
            WorkspacePathGuard(self.root), max_file_bytes=3
        )

        with self.assertRaises(FileTooLargeError):
            editor.plan_write("large.txt", "replacement")

    def test_plan_write_previews_diff_without_touching_disk(self) -> None:
        target = self.root / "note.txt"
        target.write_text("old\n", encoding="utf-8")
        original_bytes = target.read_bytes()

        plan = self.editor.plan_write("note.txt", "new\n")

        self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(plan.relative_path, "note.txt")
        self.assertTrue(plan.existed)
        self.assertEqual(plan.before_sha256, hashlib.sha256(original_bytes).hexdigest())
        self.assertIn("--- a/note.txt", plan.diff)
        self.assertIn("+++ b/note.txt", plan.diff)
        self.assertIn("-old", plan.diff)
        self.assertIn("+new", plan.diff)
        with self.assertRaises(FrozenInstanceError):
            plan.after_text = "changed"  # type: ignore[misc]

    def test_diff_separates_lines_without_trailing_newlines(self) -> None:
        target = self.root / "no-newline.txt"
        target.write_bytes(b"old")

        plan = self.editor.plan_write("no-newline.txt", "new")

        self.assertEqual(plan.after_text, "new")
        self.assertIn("-old\n\\ No newline at end of file\n", plan.diff)
        self.assertIn("+new\n\\ No newline at end of file\n", plan.diff)
        self.assertNotIn("-old+new", plan.diff)

    def test_plan_write_and_apply_create_a_new_file(self) -> None:
        plan = self.editor.plan_write("created.txt", "created\n")

        self.assertFalse(plan.existed)
        self.assertIsNone(plan.before_sha256)
        self.assertIn("--- /dev/null", plan.diff)
        self.assertFalse((self.root / "created.txt").exists())

        self.editor.apply(plan)

        self.assertEqual((self.root / "created.txt").read_text(encoding="utf-8"), "created\n")

    def test_plan_replace_requires_one_nonempty_occurrence(self) -> None:
        target = self.root / "replace.txt"
        target.write_text("alpha beta alpha\n", encoding="utf-8")

        for old_text in ("", "missing", "alpha"):
            with self.subTest(old_text=old_text):
                with self.assertRaises(ValueError):
                    self.editor.plan_replace("replace.txt", old_text, "new")
        self.assertEqual(target.read_text(encoding="utf-8"), "alpha beta alpha\n")

    def test_replace_applies_when_text_occurs_exactly_once(self) -> None:
        target = self.root / "replace.txt"
        target.write_text("alpha beta\n", encoding="utf-8")

        plan = self.editor.plan_replace("replace.txt", "beta", "gamma")
        self.editor.apply(plan)

        self.assertEqual(target.read_text(encoding="utf-8"), "alpha gamma\n")

    def test_expected_and_stale_hashes_raise_conflicts_without_overwrite(self) -> None:
        target = self.root / "conflict.txt"
        target.write_text("first\n", encoding="utf-8")
        wrong_hash = hashlib.sha256(b"wrong").hexdigest()

        with self.assertRaises(EditConflictError):
            self.editor.plan_write(
                "conflict.txt", "planned\n", expected_sha256=wrong_hash
            )

        plan = self.editor.plan_write("conflict.txt", "planned\n")
        target.write_text("concurrent\n", encoding="utf-8")
        with self.assertRaises(EditConflictError):
            self.editor.apply(plan)
        self.assertEqual(target.read_text(encoding="utf-8"), "concurrent\n")


class AtomicApplyTests(WorkspaceEditorTestCase):
    def test_replace_failure_cleans_same_directory_temporary_file(self) -> None:
        target = self.root / "atomic.txt"
        target.write_text("before\n", encoding="utf-8")
        plan = self.editor.plan_write("atomic.txt", "after\n")
        before_names = {item.name for item in self.root.iterdir()}

        if os.name == "nt":
            replace_path = (
                "code_agent.workspace._windows_atomic_replace._replace_file"
            )
        else:
            replace_path = "code_agent.workspace._secure_replace._posix_io.replace"
        with patch(replace_path, side_effect=OSError("busy")):
            with self.assertRaises(WorkspaceError):
                self.editor.apply(plan)

        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
        self.assertEqual({item.name for item in self.root.iterdir()}, before_names)


class SnapshotTests(WorkspaceEditorTestCase):
    def test_restore_plan_adds_sorted_tombstones_for_new_files(self) -> None:
        target = WorkspaceSnapshot((SnapshotEntry("kept.py", b"old", True),))

        restore = build_restore_snapshot(("kept.py", "created.py"), target)

        self.assertEqual(
            [(entry.relative_path, entry.existed) for entry in restore.entries],
            [("created.py", False), ("kept.py", True)],
        )

    def test_restore_plan_rejects_duplicate_current_or_target_paths(self) -> None:
        target = WorkspaceSnapshot(
            (
                SnapshotEntry("same.py", b"one", True),
                SnapshotEntry("same.py", b"two", True),
            )
        )

        with self.assertRaisesRegex(ValueError, "duplicate target path"):
            build_restore_snapshot(("same.py",), target)
        with self.assertRaisesRegex(ValueError, "duplicate current path"):
            build_restore_snapshot(("same.py", "same.py"), WorkspaceSnapshot(()))

    def test_snapshot_entry_rejects_non_byte_blob(self) -> None:
        with self.assertRaisesRegex(TypeError, "content must be bytes"):
            SnapshotEntry("bad.py", "text", True)  # type: ignore[arg-type]

    def test_restore_preflights_every_sensitive_path_before_writing(self) -> None:
        first = self.root / "first.py"
        first.write_bytes(b"before")
        snapshot = WorkspaceSnapshot(
            (
                SnapshotEntry("first.py", b"after", True),
                SnapshotEntry(".git/last.py", b"last", True),
            )
        )

        with self.assertRaises(WorkspaceError):
            self.editor.restore(snapshot)

        self.assertEqual(first.read_bytes(), b"before")

    def test_restore_preflights_every_delete_target_before_writing(self) -> None:
        first = self.root / "first.py"
        first.write_bytes(b"before")
        (self.root / "directory").mkdir()
        snapshot = WorkspaceSnapshot(
            (
                SnapshotEntry("first.py", b"after", True),
                SnapshotEntry("directory", None, False),
            )
        )

        with self.assertRaisesRegex(WorkspaceError, "not a file"):
            self.editor.restore(snapshot)

        self.assertEqual(first.read_bytes(), b"before")

    def test_snapshot_stat_precheck_rejects_before_read_bytes(self) -> None:
        (self.root / "large.bin").write_bytes(b"123456")

        with patch(
            "code_agent.workspace._guarded_read._read_bounded",
            side_effect=AssertionError("oversized file was read"),
        ):
            with self.assertRaises(FileTooLargeError):
                self.editor.snapshot(("large.bin",), max_total_bytes=5)

    def test_snapshot_detects_growth_while_reading_remaining_plus_one(self) -> None:
        (self.root / "growing.bin").write_bytes(b"123")

        with patch(
            "code_agent.workspace._guarded_read._read_bounded",
            return_value=b"1234",
        ):
            with self.assertRaises(FileTooLargeError):
                self.editor.snapshot(("growing.bin",), max_total_bytes=3)

    def test_snapshot_uses_verified_open_handle_identity(self) -> None:
        (self.root / "file.bin").write_bytes(b"content")

        with patch(
            "code_agent.workspace._guarded_read._verify_handle",
            side_effect=WorkspaceError("identity changed"),
        ) as verify:
            with self.assertRaises(WorkspaceError):
                self.editor.snapshot(("file.bin",))

        verify.assert_called_once()

    def test_snapshot_rejects_opened_handle_redirected_outside(self) -> None:
        (self.root / "file.bin").write_bytes(b"content")

        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory) / "outside.bin"
            outside.write_bytes(b"secret")
            with patch(
                "code_agent.workspace._guarded_read._final_handle_path",
                return_value=outside,
            ):
                with self.assertRaises(WorkspaceError):
                    self.editor.snapshot(("file.bin",))

    def test_opened_then_disappearing_snapshot_is_not_missing(self) -> None:
        (self.root / "file.bin").write_bytes(b"content")

        with patch(
            "code_agent.workspace._guarded_read._verify_handle",
            side_effect=FileNotFoundError("removed after open"),
        ):
            with self.assertRaises(WorkspaceError):
                self.editor.snapshot(("file.bin",))

    def test_restore_reverses_updates_deletions_and_creations(self) -> None:
        updated = self.root / "updated.bin"
        deleted = self.root / "deleted.txt"
        created = self.root / "created.txt"
        updated.write_bytes(b"\xef\xbb\xbforiginal")
        deleted.write_bytes(b"restore me")
        snapshot = self.editor.snapshot(
            ("updated.bin", "deleted.txt", "created.txt")
        )

        updated.write_bytes(b"changed")
        deleted.unlink()
        created.write_bytes(b"remove me")
        self.editor.restore(snapshot)

        self.assertEqual(updated.read_bytes(), b"\xef\xbb\xbforiginal")
        self.assertEqual(deleted.read_bytes(), b"restore me")
        self.assertFalse(created.exists())
        self.assertEqual([entry.existed for entry in snapshot.entries], [True, True, False])
        with self.assertRaises(FrozenInstanceError):
            snapshot.entries = ()  # type: ignore[misc]

    def test_snapshot_enforces_aggregate_byte_limit(self) -> None:
        (self.root / "one.bin").write_bytes(b"123")
        (self.root / "two.bin").write_bytes(b"456")

        with self.assertRaises(FileTooLargeError):
            self.editor.snapshot(("one.bin", "two.bin"), max_total_bytes=5)
        snapshot = self.editor.snapshot(
            ("one.bin", "two.bin"), max_total_bytes=6
        )
        self.assertEqual(sum(len(entry.content or b"") for entry in snapshot.entries), 6)

if __name__ == "__main__":
    unittest.main()
