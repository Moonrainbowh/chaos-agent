from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace._git_worktrees import FixedGitWorktreeCommands  # noqa: E402
from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace.tests.test_worktrees import (  # noqa: E402
    WorktreeTestCase,
    run_git,
)
from code_agent.workspace.worktrees import WorktreeManager  # noqa: E402


class WorktreeLeaseTests(WorktreeTestCase):
    def test_claimed_dirty_worktree_cannot_use_unclaimed_cleanup(self) -> None:
        manager = WorktreeManager(self.storage)
        lease = manager.create_unclaimed(
            self.source, "lineage-1", "codex/task-lineage-1"
        )
        worktree = manager.claim_unclaimed(lease)
        (worktree.root / "unsaved.txt").write_text("active", encoding="utf-8")

        with self.assertRaises(WorkspaceError):
            manager.discard_unclaimed(lease)

        self.assertTrue(worktree.root.exists())
        self.assertEqual(
            run_git(self.source, "rev-parse", worktree.branch_name),
            worktree.head_commit,
        )

    def test_plain_managed_record_is_not_an_unclaimed_capability(self) -> None:
        manager = WorktreeManager(self.storage)
        worktree = manager.create(
            self.source, "lineage-1", "codex/task-lineage-1"
        )
        (worktree.root / "unsaved.txt").write_text("active", encoding="utf-8")

        with self.assertRaises((TypeError, WorkspaceError)):
            manager.discard_unclaimed(worktree)  # type: ignore[arg-type]

        self.assertTrue(worktree.root.exists())

    def test_branch_advance_wins_against_conditional_cleanup(self) -> None:
        manager = WorktreeManager(self.storage)
        lease = manager.create_unclaimed(
            self.source, "lineage-1", "codex/task-lineage-1"
        )
        worktree = lease.worktree
        (self.source / "tracked.txt").write_bytes(b"advanced\n")
        run_git(self.source, "add", "tracked.txt")
        run_git(self.source, "commit", "-q", "-m", "advance source")
        advanced = run_git(self.source, "rev-parse", "HEAD")
        real_delete = FixedGitWorktreeCommands.delete_branch_if_matches

        def advance_before_delete(commands, branch: str, expected: str) -> None:
            run_git(
                self.source,
                "update-ref",
                f"refs/heads/{branch}",
                advanced,
                expected,
            )
            real_delete(commands, branch, expected)

        with patch.object(
            FixedGitWorktreeCommands,
            "delete_branch_if_matches",
            advance_before_delete,
        ):
            with self.assertRaises(WorkspaceError):
                manager.discard_unclaimed(lease)

        self.assertFalse(worktree.root.exists())
        self.assertEqual(
            run_git(self.source, "rev-parse", worktree.branch_name), advanced
        )


if __name__ == "__main__":
    unittest.main()
