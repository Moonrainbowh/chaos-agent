from __future__ import annotations

import tempfile
import subprocess
import unittest
from pathlib import Path

from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.git import GitWorkspace
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent_win.edit_plan_store import WorkspaceEditPlanStore
from code_agent_win.workspace_edit_planning import (
    WorkspaceEditPlanningError,
    create_stored_edit_plan,
)


_FINGERPRINT = "a" * 64


class _Git:
    def __init__(
        self, changed: tuple[str, ...], tracked: tuple[str, ...] | None = None
    ) -> None:
        self.changed = changed
        self.tracked = tracked

    def changed_snapshot_paths(self) -> tuple[str, ...]:
        return self.changed

    def tracked_paths(self, paths: tuple[str, ...]) -> tuple[str, ...]:
        return paths if self.tracked is None else self.tracked


class WorkspaceEditPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "pkg").mkdir()
        (self.root / "a.py").write_text("before\n", encoding="utf-8")
        (self.root / "delete.py").write_text("delete\n", encoding="utf-8")
        (self.root / "move.py").write_text("move\n", encoding="utf-8")
        self.editor = WorkspaceEditor(WorkspacePathGuard(self.root))
        identifiers = iter(("1" * 32, "2" * 32))
        self.store = WorkspaceEditPlanStore(id_factory=lambda: next(identifiers))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_multi_operation_plan_is_zero_write_and_records_risks(self) -> None:
        planned = create_stored_edit_plan(
            self.editor,
            self.store,
            (
                {"kind": "replace", "path": "a.py", "old_text": "before", "new_text": "after"},
                {"kind": "delete", "path": "delete.py"},
                {"kind": "move", "source_path": "move.py", "destination_path": "pkg/move.py"},
                {"kind": "write", "path": "created.py", "content": ""},
            ),
            workspace_fingerprint=_FINGERPRINT,
            owner_thread_id="thread-1",
            task_id="task-1",
            git=_Git(("a.py",)),
        )

        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "before\n")
        self.assertTrue((self.root / "delete.py").exists())
        self.assertTrue((self.root / "move.py").exists())
        self.assertFalse((self.root / "pkg" / "move.py").exists())
        self.assertFalse((self.root / "created.py").exists())
        self.assertEqual(planned.plan_id, "1" * 32)
        self.assertEqual(planned.plan_digest, planned.batch.plan_id)
        self.assertEqual(
            planned.risk_flags,
            ("dirty_baseline", "delete", "move"),
        )
        self.assertEqual(planned.dirty_paths, ("a.py",))
        self.assertEqual(planned.operation_count, 4)
        self.assertEqual(planned.path_count, 5)
        self.assertIn("rename from move.py", planned.combined_diff)

    def test_empty_write_and_empty_replacement_are_valid(self) -> None:
        planned = create_stored_edit_plan(
            self.editor,
            self.store,
            (
                {"kind": "write", "path": "empty.txt", "content": ""},
                {"kind": "replace", "path": "a.py", "old_text": "before", "new_text": ""},
            ),
            workspace_fingerprint=_FINGERPRINT,
            owner_thread_id="thread-1",
            task_id=None,
            git=_Git(()),
        )
        self.assertEqual(planned.operation_count, 2)
        self.assertEqual(planned.risk_flags, ())

    def test_non_git_existing_mutation_requires_explicit_risk(self) -> None:
        planned = create_stored_edit_plan(
            self.editor,
            self.store,
            ({"kind": "write", "path": "a.py", "content": "after\n"},),
            workspace_fingerprint=_FINGERPRINT,
            owner_thread_id="thread-1",
            task_id=None,
            git=None,
        )
        self.assertEqual(planned.risk_flags, ("non_git_existing",))

    def test_git_untracked_existing_mutation_requires_explicit_risk(self) -> None:
        planned = create_stored_edit_plan(
            self.editor,
            self.store,
            ({"kind": "write", "path": "a.py", "content": "after\n"},),
            workspace_fingerprint=_FINGERPRINT,
            owner_thread_id="thread-1",
            task_id=None,
            git=_Git((), tracked=()),
        )
        self.assertEqual(planned.risk_flags, ("untracked_existing",))

    def test_git_ignored_existing_file_cannot_bypass_explicit_risk(self) -> None:
        subprocess.run(
            ["git", "init", "-q"], cwd=self.root, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
        )
        (self.root / ".gitignore").write_text("a.py\n", encoding="utf-8")

        planned = create_stored_edit_plan(
            self.editor,
            self.store,
            ({"kind": "write", "path": "a.py", "content": "after\n"},),
            workspace_fingerprint=_FINGERPRINT,
            owner_thread_id="thread-1",
            task_id=None,
            git=GitWorkspace(self.root),
        )

        self.assertEqual(planned.dirty_paths, ())
        self.assertEqual(planned.risk_flags, ("untracked_existing",))

    def test_more_than_32_affected_paths_is_rejected_before_store_write(self) -> None:
        operations = []
        for index in range(17):
            source = f"move-{index}.txt"
            (self.root / source).write_text(str(index), encoding="utf-8")
            operations.append(
                {
                    "kind": "move",
                    "source_path": source,
                    "destination_path": f"pkg/{source}",
                }
            )
        with self.assertRaises(WorkspaceEditPlanningError) as raised:
            create_stored_edit_plan(
                self.editor,
                self.store,
                tuple(operations),
                workspace_fingerprint=_FINGERPRINT,
                owner_thread_id="thread-1",
                task_id=None,
                git=_Git(()),
            )
        self.assertEqual(raised.exception.code, "edit_plan_too_large")


if __name__ == "__main__":
    unittest.main()
