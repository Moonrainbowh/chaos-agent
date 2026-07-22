from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch

from code_agent.workspace.edits import (
    EditPlan,
    SnapshotEntry,
    WorkspaceEditor,
    WorkspaceSnapshot,
)
from code_agent.workspace.errors import EditConflictError, FileTooLargeError
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.rewind_state import (
    PreparedEditState,
    WorkspaceFileState,
    observe_file_states,
    prepare_edit_state,
    relevant_path_digest,
)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class RewindStateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.editor = WorkspaceEditor(WorkspacePathGuard(self.root))

    def tearDown(self) -> None:
        self.temporary.cleanup()


class PrepareEditStateTests(RewindStateTestCase):
    def test_prepare_edit_state_preserves_missing_preimage(self) -> None:
        plan = self.editor.plan_write("future.txt", "created\n")

        prepared = prepare_edit_state(self.editor, plan)

        self.assertIsInstance(prepared, PreparedEditState)
        self.assertEqual(
            prepared.before,
            WorkspaceFileState("future.txt", False, None, 0),
        )
        self.assertEqual(
            prepared.after,
            WorkspaceFileState(
                "future.txt",
                True,
                sha256(plan.after_text.encode("utf-8")),
                len(plan.after_text.encode("utf-8")),
            ),
        )
        self.assertEqual(len(prepared.snapshot.entries), 1)
        entry = prepared.snapshot.entries[0]
        self.assertEqual(
            (entry.relative_path, entry.existed, entry.content),
            ("future.txt", False, None),
        )

    def test_prepare_edit_state_preserves_dirty_binary_preimage(self) -> None:
        content = b"\x00dirty\xff"
        (self.root / "dirty.bin").write_bytes(content)
        plan = EditPlan("dirty.bin", sha256(content), "replacement", "", True)

        prepared = prepare_edit_state(self.editor, plan)

        self.assertEqual(
            prepared.before,
            WorkspaceFileState("dirty.bin", True, sha256(content), len(content)),
        )
        self.assertEqual(prepared.snapshot.entries[0].content, content)

    def test_prepare_edit_state_rejects_stale_plan(self) -> None:
        target = self.root / "note.txt"
        target.write_text("before\n", encoding="utf-8")
        plan = self.editor.plan_write("note.txt", "after\n")
        target.write_bytes(b"concurrent")

        with self.assertRaises(EditConflictError):
            prepare_edit_state(self.editor, plan)

    def test_prepare_edit_state_never_applies_the_plan(self) -> None:
        target = self.root / "note.txt"
        target.write_text("before\n", encoding="utf-8")
        plan = self.editor.plan_write("note.txt", "after\n")

        with patch.object(
            self.editor,
            "apply",
            side_effect=AssertionError("prepare must not apply"),
        ) as apply:
            prepare_edit_state(self.editor, plan)

        apply.assert_not_called()
        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")


class ObserveFileStatesTests(RewindStateTestCase):
    def test_observe_file_states_sorts_canonical_paths(self) -> None:
        (self.root / "nested").mkdir()
        (self.root / "a.bin").write_bytes(b"a")
        (self.root / "z.bin").write_bytes(b"z")

        observed = observe_file_states(
            self.editor,
            ("z.bin", "nested/../a.bin"),
        )

        self.assertEqual(
            tuple(state.relative_path for state in observed),
            ("a.bin", "z.bin"),
        )

    def test_observe_file_states_rejects_duplicate_canonical_paths(self) -> None:
        (self.root / "same.bin").write_bytes(b"same")

        with self.assertRaises(ValueError):
            observe_file_states(self.editor, ("same.bin", "./same.bin"))

    def test_observe_file_states_never_calls_editor_apply(self) -> None:
        target = self.root / "note.bin"
        target.write_bytes(b"unchanged")

        with patch.object(
            self.editor,
            "apply",
            side_effect=AssertionError("observe must not apply"),
        ) as apply, patch.object(
            self.editor,
            "snapshot",
            side_effect=AssertionError("observe must use the trusted implementation"),
        ) as snapshot:
            observed = observe_file_states(self.editor, ("note.bin",))

        apply.assert_not_called()
        snapshot.assert_not_called()
        self.assertEqual(observed[0].sha256, sha256(b"unchanged"))
        self.assertEqual(target.read_bytes(), b"unchanged")
        with patch.object(self.editor.guard, "resolve") as resolve:
            with self.assertRaises(TypeError):
                observe_file_states(self.editor, ("note.bin",))
        resolve.assert_not_called()

    def test_observation_budget_failure_returns_no_partial_state(self) -> None:
        (self.root / "one.bin").write_bytes(b"123")
        (self.root / "two.bin").write_bytes(b"456")
        result: tuple[WorkspaceFileState, ...] | None = None

        with self.assertRaises(FileTooLargeError):
            result = observe_file_states(
                self.editor,
                ("one.bin", "two.bin"),
                max_total_bytes=5,
            )

        self.assertIsNone(result)


class RelevantPathDigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.first = WorkspaceFileState("a.bin", True, "a" * 64, 1)
        self.second = WorkspaceFileState("b.bin", False, None, 0)

    def test_relevant_digest_is_stable_for_equivalent_order(self) -> None:
        forward = relevant_path_digest((self.first, self.second))
        reverse = relevant_path_digest((self.second, self.first))

        self.assertEqual(forward, reverse)
        self.assertEqual(len(forward), 64)

    def test_relevant_digest_changes_with_existence_hash_or_size(self) -> None:
        empty_digest = sha256(b"")
        variants = (
            WorkspaceFileState("b.bin", True, empty_digest, 0),
            replace(self.first, sha256="b" * 64),
            replace(self.first, size=2),
        )
        baselines = (
            relevant_path_digest((self.second,)),
            relevant_path_digest((self.first,)),
            relevant_path_digest((self.first,)),
        )

        for baseline, variant in zip(baselines, variants):
            with self.subTest(variant=variant):
                self.assertNotEqual(
                    baseline,
                    relevant_path_digest((variant,)),
                )

    def test_relevant_digest_rejects_duplicate_identity(self) -> None:
        with self.assertRaises(ValueError):
            relevant_path_digest((self.first, self.first))


class ModelAndBoundaryValidationTests(RewindStateTestCase):
    def test_models_are_frozen_and_validate_invariants(self) -> None:
        valid = WorkspaceFileState("dir/file.bin", True, "a" * 64, 1)
        with self.assertRaises(FrozenInstanceError):
            valid.size = 2  # type: ignore[misc]

        invalid_values = (
            ("dir/../file.bin", True, "a" * 64, 1),
            ("file.bin", False, "a" * 64, 0),
            ("file.bin", False, None, 1),
            ("file.bin", True, None, 0),
            ("file.bin", True, "A" * 64, 0),
            ("file.bin", True, "a" * 63, 0),
            ("file.bin", 1, "a" * 64, 1),
            ("file.bin", True, "a" * 64, True),
            ("bad\ud800.txt", False, None, 0),
            ("file.bin:stream", False, None, 0),
        )
        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises((TypeError, ValueError)):
                    WorkspaceFileState(*values)  # type: ignore[arg-type]
        before = WorkspaceFileState("file.bin", True, sha256(b"x"), 1)
        after = WorkspaceFileState("file.bin", True, sha256(b"after"), 5)
        invalid_entries = (
            lambda: SnapshotEntry("file.bin", bytearray(b"x"), True),  # type: ignore[arg-type]
            lambda: SnapshotEntry("file.bin", b"x", 1),  # type: ignore[arg-type]
            lambda: SnapshotEntry("file.bin", None, 0),  # type: ignore[arg-type]
        )
        for entry_factory in invalid_entries:
            with self.subTest(entry=entry_factory):
                with self.assertRaises((TypeError, ValueError)):
                    entry = entry_factory()
                    PreparedEditState(WorkspaceSnapshot((entry,)), before, after)

    def test_prepared_edit_rejects_snapshot_before_mismatch(self) -> None:
        content = b"x"
        before = WorkspaceFileState("file.bin", True, sha256(content), 1)
        after = WorkspaceFileState("file.bin", True, sha256(b"after"), 5)
        mismatches = (
            (WorkspaceSnapshot(()), before),
            (
                WorkspaceSnapshot((
                    SnapshotEntry("file.bin", content, True),
                    SnapshotEntry("other.bin", content, True),
                )),
                before,
            ),
            (WorkspaceSnapshot((SnapshotEntry("other.bin", content, True),)), before),
            (WorkspaceSnapshot((SnapshotEntry("file.bin", b"y", True),)), before),
            (WorkspaceSnapshot((SnapshotEntry("file.bin", None, False),)), before),
            (WorkspaceSnapshot((SnapshotEntry("file.bin", content, True),)),
             WorkspaceFileState("file.bin", True, "a" * 64, 1)),
            (WorkspaceSnapshot((SnapshotEntry("file.bin", content, True),)),
             WorkspaceFileState("file.bin", True, sha256(content), 2)),
        )
        for snapshot, mismatched_before in mismatches:
            with self.subTest(snapshot=snapshot, before=mismatched_before):
                with self.assertRaises((TypeError, ValueError)):
                    PreparedEditState(snapshot, mismatched_before, after)

    def test_public_functions_strictly_validate_typed_inputs(self) -> None:
        plan = self.editor.plan_write("future.txt", "created")

        for invalid in (True, 1.5, -1):
            with self.subTest(max_total_bytes=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    prepare_edit_state(
                        self.editor,
                        plan,
                        max_total_bytes=invalid,  # type: ignore[arg-type]
                    )
                with self.assertRaises((TypeError, ValueError)):
                    observe_file_states(
                        self.editor,
                        (),
                        max_total_bytes=invalid,  # type: ignore[arg-type]
                    )

        with self.assertRaises(TypeError):
            prepare_edit_state(self.editor, object())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            observe_file_states(self.editor, ["future.txt"])  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            relevant_path_digest([self])  # type: ignore[arg-type]
        class DerivedEditor(WorkspaceEditor):
            pass
        with self.assertRaises(TypeError):
            observe_file_states(DerivedEditor(self.editor.guard), ())


if __name__ == "__main__":
    unittest.main()
