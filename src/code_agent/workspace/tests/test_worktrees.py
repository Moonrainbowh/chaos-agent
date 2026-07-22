from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace.git import GitWorkspace  # noqa: E402
from code_agent.workspace.worktrees import WorktreeManager  # noqa: E402


def run_git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        env=os.environ.copy(),
    )
    return result.stdout.decode("utf-8").strip()


class WorktreeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        self.storage = self.root / "managed"
        self.source.mkdir()
        self.storage.mkdir()
        self.environment = patch.dict(
            os.environ,
            {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "HOME": str(self.root / "home"),
                "XDG_CONFIG_HOME": str(self.root / "xdg"),
            },
        )
        self.environment.start()
        run_git(self.source, "init", "-q")
        run_git(self.source, "config", "user.name", "Worktree Tests")
        run_git(self.source, "config", "user.email", "worktree@example.invalid")
        (self.source / "tracked.txt").write_bytes(b"base\n")
        run_git(self.source, "add", "tracked.txt")
        run_git(self.source, "commit", "-q", "-m", "initial")

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()


class WorktreeCreateTests(WorktreeTestCase):
    def test_create_uses_bound_branch_without_changing_source(self) -> None:
        source_git = GitWorkspace(self.source)
        before_root = run_git(self.source, "rev-parse", "--show-toplevel")
        before_head = run_git(self.source, "rev-parse", "HEAD")
        before_status = source_git.status_porcelain()

        created = WorktreeManager(self.storage).create(
            self.source,
            "lineage-1",
            "codex/task-lineage-1",
        )

        self.assertTrue((created.root / ".git").is_file())
        self.assertEqual(created.source_root, self.source)
        self.assertEqual(created.head_commit, before_head)
        self.assertEqual(run_git(created.root, "branch", "--show-current"), created.branch_name)
        self.assertEqual(run_git(self.source, "rev-parse", "--show-toplevel"), before_root)
        self.assertEqual(run_git(self.source, "rev-parse", "HEAD"), before_head)
        self.assertEqual(source_git.status_porcelain(), before_status)

    def test_repository_identity_is_stable_for_linked_worktree(self) -> None:
        linked = self.root / "linked"
        run_git(self.source, "worktree", "add", "-q", "-b", "linked", str(linked), "HEAD")
        manager = WorktreeManager(self.storage)

        primary = manager.identify(self.source)
        secondary = manager.identify(linked)

        self.assertEqual(primary, secondary)
        self.assertTrue(primary.common_dir.is_absolute())
        self.assertEqual(len(primary.repository_id), 64)

    def test_create_from_linked_source_keeps_linked_head_and_status(self) -> None:
        linked = self.root / "linked"
        run_git(self.source, "worktree", "add", "-q", "-b", "linked", str(linked), "HEAD")
        (linked / "untracked.txt").write_text("source", encoding="utf-8")
        before_head = run_git(linked, "rev-parse", "HEAD")
        before_status = GitWorkspace(linked).status_porcelain()

        created = WorktreeManager(self.storage).create(
            linked, "linked-task", "codex/task-linked-task"
        )

        self.assertEqual(created.source_root, linked.resolve())
        self.assertEqual(run_git(linked, "rev-parse", "HEAD"), before_head)
        self.assertEqual(GitWorkspace(linked).status_porcelain(), before_status)

    def test_create_rejects_branch_collision(self) -> None:
        run_git(self.source, "branch", "codex/task-lineage-1")

        with self.assertRaisesRegex(WorkspaceError, "already exists"):
            WorktreeManager(self.storage).create(
                self.source, "lineage-1", "codex/task-lineage-1"
            )

    def test_create_rejects_non_repository(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()

        with self.assertRaisesRegex(WorkspaceError, "not a Git"):
            WorktreeManager(self.storage).create(
                plain, "lineage-1", "codex/task-lineage-1"
            )

    def test_create_reports_missing_git(self) -> None:
        with patch("code_agent.workspace.git.shutil.which", return_value=None):
            with self.assertRaisesRegex(WorkspaceError, "not found"):
                WorktreeManager(self.storage).create(
                    self.source, "lineage-1", "codex/task-lineage-1"
                )


if __name__ == "__main__":
    unittest.main()
