from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace._git_worktrees import FixedGitWorktreeCommands  # noqa: E402
from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace.tests.test_worktrees import WorktreeTestCase, run_git  # noqa: E402
from code_agent.workspace.worktrees import WorktreeManager  # noqa: E402


class CompensationLinkSwapTests(WorktreeTestCase):
    def test_same_repository_link_swap_never_force_removes_external_worktree(self) -> None:
        external = self.root / "external-worktree"
        displaced = self.root / "displaced-created-worktree"
        state: dict[str, str] = {}
        real_add = FixedGitWorktreeCommands.add
        real_force_remove = FixedGitWorktreeCommands.force_remove

        def add_then_swap(commands, branch, target, head):
            real_add(commands, branch, target, head)
            target.rename(displaced)
            run_git(
                self.source,
                "worktree",
                "add",
                "-q",
                "-b",
                "external-branch",
                str(external),
                "HEAD",
            )
            state["head"] = run_git(external, "rev-parse", "HEAD")
            state["status"] = run_git(external, "status", "--porcelain")
            _directory_link(target, external)

        with patch.object(FixedGitWorktreeCommands, "add", add_then_swap), patch.object(
            FixedGitWorktreeCommands,
            "force_remove",
            autospec=True,
            side_effect=real_force_remove,
        ) as force_remove:
            with self.assertRaises(WorkspaceError) as raised:
                WorktreeManager(self.storage).create(
                    self.source, "lineage-1", "codex/task-lineage-1"
                )

        force_remove.assert_not_called()
        self.assertTrue(any("skipped" in item for item in raised.exception.cleanup_errors))
        self.assertEqual(run_git(external, "rev-parse", "HEAD"), state["head"])
        self.assertEqual(run_git(external, "status", "--porcelain"), state["status"])
        self.assertIn("external-branch", run_git(self.source, "branch", "--list"))
        self.assertIn("codex/task-lineage-1", run_git(self.source, "branch", "--list"))


class RepositoryLifecycleLockTests(WorktreeTestCase):
    def test_two_managers_serialize_same_repository_creation(self) -> None:
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        errors: list[BaseException] = []
        real_add = FixedGitWorktreeCommands.add

        def gated_add(commands, branch, target, head):
            if branch.endswith("lineage-1"):
                first_entered.set()
                if not release_first.wait(3):
                    raise AssertionError("first create was not released")
            else:
                second_entered.set()
            return real_add(commands, branch, target, head)

        def create(manager, lineage):
            try:
                manager.create(self.source, lineage, f"codex/task-{lineage}")
            except BaseException as error:
                errors.append(error)

        with patch.object(FixedGitWorktreeCommands, "add", gated_add):
            first = threading.Thread(target=create, args=(WorktreeManager(self.storage), "lineage-1"))
            second = threading.Thread(target=create, args=(WorktreeManager(self.storage), "lineage-2"))
            first.start()
            self.assertTrue(first_entered.wait(2))
            second.start()
            try:
                self.assertFalse(second_entered.wait(1.0))
            finally:
                release_first.set()
                first.join(5)
                second.join(5)

        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(second_entered.is_set())

    def test_second_manager_lock_timeout_has_no_git_side_effect(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []
        real_add = FixedGitWorktreeCommands.add

        def blocked_add(commands, branch, target, head):
            entered.set()
            if not release.wait(3):
                raise AssertionError("holder was not released")
            return real_add(commands, branch, target, head)

        def hold_lock():
            try:
                WorktreeManager(self.storage).create(
                    self.source, "lineage-1", "codex/task-lineage-1"
                )
            except BaseException as error:
                errors.append(error)

        with patch.object(FixedGitWorktreeCommands, "add", blocked_add):
            holder = threading.Thread(target=hold_lock)
            holder.start()
            self.assertTrue(entered.wait(2))
            try:
                contender = WorktreeManager(self.storage, lock_timeout_s=0.05)
                with self.assertRaisesRegex(WorkspaceError, "lock timeout"):
                    contender.create(self.source, "lineage-2", "codex/task-lineage-2")
            finally:
                release.set()
                holder.join(5)

        self.assertEqual(errors, [])
        self.assertNotIn("codex/task-lineage-2", run_git(self.source, "branch", "--list"))

    def test_lock_timeout_blocks_remove_without_deleting_worktree(self) -> None:
        holder = WorktreeManager(self.storage)
        contender = WorktreeManager(self.storage, lock_timeout_s=0.05)
        created = holder.create(self.source, "lineage-1", "codex/task-lineage-1")

        with holder._repository_lock(created.repository_id):
            with self.assertRaisesRegex(WorkspaceError, "lock timeout"):
                contender.remove(created, confirmed=True, active=False)

        self.assertTrue(created.root.exists())

    def test_lock_timeout_does_not_misreport_prune_success(self) -> None:
        holder = WorktreeManager(self.storage)
        contender = WorktreeManager(self.storage, lock_timeout_s=0.05)
        created = holder.create(self.source, "lineage-1", "codex/task-lineage-1")
        shutil.rmtree(created.root)

        with holder._repository_lock(created.repository_id):
            with self.assertRaisesRegex(WorkspaceError, "lock timeout"):
                contender.prune((created,))

        listing = run_git(self.source, "worktree", "list", "--porcelain")
        self.assertIn(created.root.as_posix(), listing)

    def test_prune_skips_target_recreated_after_registration_check(self) -> None:
        manager = WorktreeManager(self.storage)
        created = manager.create(self.source, "lineage-1", "codex/task-lineage-1")
        shutil.rmtree(created.root)
        real_entry = FixedGitWorktreeCommands.entry
        injected = False

        def recreate_after_check(commands, target):
            nonlocal injected
            entry = real_entry(commands, target)
            if not injected and entry is not None:
                injected = True
                target.mkdir()
                (target / "unknown.txt").write_text("unknown", encoding="utf-8")
            return entry

        with patch.object(FixedGitWorktreeCommands, "entry", recreate_after_check):
            self.assertEqual(manager.prune((created,)), ())

        self.assertEqual((created.root / "unknown.txt").read_text(encoding="utf-8"), "unknown")
        self.assertIn(created.root.as_posix(), run_git(self.source, "worktree", "list", "--porcelain"))


def _directory_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        if result.returncode:
            raise AssertionError(result.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
