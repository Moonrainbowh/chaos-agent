from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace._git_worktrees import FixedGitWorktreeCommands  # noqa: E402
from code_agent.workspace.git import GitWorkspace  # noqa: E402
from code_agent.workspace.tests.test_worktrees import WorktreeTestCase, run_git  # noqa: E402
from code_agent.workspace.worktrees import WorktreeManager  # noqa: E402


class WorktreeRemoveTests(WorktreeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.manager = WorktreeManager(self.storage)
        self.created = self.manager.create(
            self.source, "lineage-1", "codex/task-lineage-1"
        )

    def test_remove_requires_confirmation_and_inactive_record(self) -> None:
        with self.assertRaisesRegex(WorkspaceError, "confirmation"):
            self.manager.remove(self.created, confirmed=False, active=False)
        with self.assertRaisesRegex(WorkspaceError, "active"):
            self.manager.remove(self.created, confirmed=True, active=True)
        self.assertTrue(self.created.root.exists())

    def test_remove_rejects_source_and_out_of_bounds_records(self) -> None:
        source_record = replace(self.created, root=self.source)
        outside_record = replace(self.created, root=self.root / "outside")

        for record in (source_record, outside_record):
            with self.subTest(root=record.root):
                with self.assertRaises(WorkspaceError):
                    self.manager.remove(record, confirmed=True, active=False)
        self.assertTrue(self.source.exists())
        self.assertTrue(self.created.root.exists())

    def test_remove_rejects_dirty_managed_worktree(self) -> None:
        (self.created.root / "tracked.txt").write_text("dirty\n", encoding="utf-8")

        with self.assertRaisesRegex(WorkspaceError, "dirty"):
            self.manager.remove(self.created, confirmed=True, active=False)

        self.assertTrue(self.created.root.exists())

    def test_remove_rejects_unknown_repository_at_managed_path(self) -> None:
        run_git(self.source, "worktree", "remove", "--force", str(self.created.root))
        self.created.root.mkdir()
        run_git(self.created.root, "init", "-q")

        with self.assertRaisesRegex(WorkspaceError, "repository"):
            self.manager.remove(self.created, confirmed=True, active=False)

        self.assertTrue((self.created.root / ".git").is_dir())

    def test_remove_keeps_root_when_git_reports_lock_failure(self) -> None:
        with patch.object(
            FixedGitWorktreeCommands,
            "remove",
            side_effect=WorkspaceError("injected locked path"),
        ):
            with self.assertRaisesRegex(WorkspaceError, "locked"):
                self.manager.remove(self.created, confirmed=True, active=False)
        self.assertTrue(self.created.root.exists())

    def test_remove_does_not_delete_residual_path_after_git_success(self) -> None:
        with patch.object(FixedGitWorktreeCommands, "remove", return_value=None):
            with self.assertRaisesRegex(WorkspaceError, "still exists"):
                self.manager.remove(self.created, confirmed=True, active=False)
        self.assertTrue(self.created.root.exists())

    def test_remove_clean_inactive_worktree_uses_fixed_command(self) -> None:
        real_popen = subprocess.Popen
        with patch("code_agent.workspace.git.subprocess.Popen", wraps=real_popen) as invoked:
            self.manager.remove(self.created, confirmed=True, active=False)

        calls = [
            call.args[0]
            for call in invoked.call_args_list
            if "worktree" in call.args[0] and "remove" in call.args[0]
        ]
        self.assertEqual(calls[-1][-3:], ["worktree", "remove", str(self.created.root)])
        self.assertNotIn("--force", calls[-1])
        self.assertFalse(self.created.root.exists())
        self.assertEqual(
            run_git(self.source, "rev-parse", "--verify", self.created.branch_name),
            self.created.head_commit,
        )


class WorktreePruneTests(WorktreeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.manager = WorktreeManager(self.storage)

    def _create(self, lineage: str):
        return self.manager.create(self.source, lineage, f"codex/task-{lineage}")

    def test_prune_never_deletes_existing_active_or_dirty_worktree(self) -> None:
        created = self._create("lineage-1")
        (created.root / "tracked.txt").write_text("dirty\n", encoding="utf-8")

        self.assertEqual(self.manager.prune((created,)), ())
        self.assertTrue(created.root.exists())

    def test_prune_ignores_out_of_bounds_and_identity_mismatch_records(self) -> None:
        created = self._create("lineage-1")
        outside = replace(created, root=self.root / "outside")
        mismatch = replace(created, repository_id="0" * 64)

        self.assertEqual(self.manager.prune((outside, mismatch)), ())
        self.assertTrue(created.root.exists())

    def test_prune_removes_only_trusted_missing_registration(self) -> None:
        created = self._create("lineage-1")
        shutil.rmtree(created.root)

        self.assertEqual(self.manager.prune((created,)), (created.root,))
        listing = run_git(self.source, "worktree", "list", "--porcelain")
        self.assertNotIn(str(created.root), listing)

    def test_prune_does_not_touch_trusted_record_if_unknown_stale_exists(self) -> None:
        trusted = self._create("lineage-1")
        unknown = self._create("lineage-2")
        shutil.rmtree(trusted.root)
        shutil.rmtree(unknown.root)

        self.assertEqual(self.manager.prune((trusted,)), ())
        listing = run_git(self.source, "worktree", "list", "--porcelain")
        self.assertIn(trusted.root.as_posix(), listing)
        self.assertIn(unknown.root.as_posix(), listing)

    def test_prune_accepts_record_already_removed_by_git(self) -> None:
        created = self._create("lineage-1")
        self.manager.remove(created, confirmed=True, active=False)

        self.assertEqual(self.manager.prune((created,)), ())


if __name__ == "__main__":
    unittest.main()
