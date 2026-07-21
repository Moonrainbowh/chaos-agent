from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace._git_worktrees import FixedGitWorktreeCommands  # noqa: E402
from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace.git import GitWorkspace  # noqa: E402
from code_agent.workspace.tests.test_git_limits import ControlledProcess  # noqa: E402
from code_agent.workspace.tests.test_worktrees import WorktreeTestCase, run_git  # noqa: E402
from code_agent.workspace.worktrees import WorktreeManager  # noqa: E402


_REPOSITORY_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
)


class GitEnvironmentIsolationTests(WorktreeTestCase):
    def _external_repository(self) -> Path:
        external = self.root / "external"
        external.mkdir()
        run_git(external, "init", "-q")
        run_git(external, "config", "user.name", "External Tests")
        run_git(external, "config", "user.email", "external@example.invalid")
        (external / "external.txt").write_text("external\n", encoding="utf-8")
        run_git(external, "add", "external.txt")
        run_git(external, "commit", "-q", "-m", "external")
        return external

    def test_repository_binding_environment_cannot_redirect_creation(self) -> None:
        external = self._external_repository()
        external_before = run_git(external, "show-ref")
        poison = {
            "GIT_DIR": str(external / ".git"),
            "GIT_WORK_TREE": str(external),
            "GIT_COMMON_DIR": str(external / ".git"),
            "GIT_INDEX_FILE": str(external / ".git" / "index"),
            "GIT_OBJECT_DIRECTORY": str(external / ".git" / "objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(external / ".git" / "objects"),
            "GIT_NAMESPACE": "poison",
            "GIT_CEILING_DIRECTORIES": str(self.root),
            "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1",
        }

        with patch.dict(os.environ, poison):
            created = WorktreeManager(self.storage).create(
                self.source, "lineage-1", "codex/task-lineage-1"
            )

        self.assertEqual((created.root / "tracked.txt").read_bytes(), b"base\n")
        self.assertEqual(run_git(external, "show-ref"), external_before)
        self.assertNotIn("codex/task-lineage-1", run_git(external, "branch", "--list"))

    def test_git_child_receives_minimal_noninteractive_environment(self) -> None:
        poison = {name: "poison" for name in _REPOSITORY_ENV}
        process = ControlledProcess(b"true\n", b"")

        with patch.dict(os.environ, poison), patch(
            "code_agent.workspace.git.subprocess.Popen", return_value=process
        ) as invoked:
            self.assertTrue(GitWorkspace(self.source).is_repository())

        child_env = invoked.call_args.kwargs["env"]
        self.assertEqual(child_env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(child_env["PATH"], os.environ["PATH"])
        self.assertTrue(all(name not in child_env for name in _REPOSITORY_ENV))


class WorktreePostcheckTests(WorktreeTestCase):
    def test_source_must_be_exact_git_top_level(self) -> None:
        child = self.source / "child"
        child.mkdir()

        with self.assertRaisesRegex(WorkspaceError, "top-level"):
            WorktreeManager(self.storage).create(
                child, "lineage-1", "codex/task-lineage-1"
            )

    def test_disappearing_target_is_compensated_after_successful_add(self) -> None:
        real_add = FixedGitWorktreeCommands.add

        def add_then_remove(commands, branch, target, head):
            real_add(commands, branch, target, head)
            shutil.rmtree(target)

        with patch.object(FixedGitWorktreeCommands, "add", add_then_remove):
            with self.assertRaises(WorkspaceError):
                WorktreeManager(self.storage).create(
                    self.source, "lineage-1", "codex/task-lineage-1"
                )

        listing = run_git(self.source, "worktree", "list", "--porcelain")
        self.assertNotIn("lineage-1", listing)
        self.assertNotIn("codex/task-lineage-1", run_git(self.source, "branch", "--list"))

    def test_wrong_target_branch_is_rejected_and_compensated(self) -> None:
        real_add = FixedGitWorktreeCommands.add

        def add_then_detach(commands, branch, target, head):
            real_add(commands, branch, target, head)
            run_git(target, "checkout", "-q", "--detach", head)

        with patch.object(FixedGitWorktreeCommands, "add", add_then_detach):
            with self.assertRaisesRegex(WorkspaceError, "postcheck"):
                WorktreeManager(self.storage).create(
                    self.source, "lineage-1", "codex/task-lineage-1"
                )

        self.assertNotIn("lineage-1", run_git(self.source, "worktree", "list", "--porcelain"))

    def test_wrong_target_repository_is_preserved_and_reports_cleanup(self) -> None:
        real_add = FixedGitWorktreeCommands.add

        def add_then_replace(commands, branch, target, head):
            real_add(commands, branch, target, head)
            run_git(self.source, "worktree", "remove", "--force", str(target))
            target.mkdir()
            run_git(target, "init", "-q")

        with patch.object(FixedGitWorktreeCommands, "add", add_then_replace):
            with self.assertRaisesRegex(WorkspaceError, "repository") as raised:
                WorktreeManager(self.storage).create(
                    self.source, "lineage-1", "codex/task-lineage-1"
                )

        self.assertTrue((raised.exception.cleanup_errors))
        repository_id = WorktreeManager(self.storage).identify(self.source).repository_id
        self.assertTrue((self.storage / repository_id / "lineage-1" / ".git").is_dir())
        self.assertIn("codex/task-lineage-1", run_git(self.source, "branch", "--list"))

    def test_advanced_target_head_is_rejected_without_deleting_new_commit(self) -> None:
        real_add = FixedGitWorktreeCommands.add

        def add_then_commit(commands, branch, target, head):
            real_add(commands, branch, target, head)
            (target / "tracked.txt").write_text("advanced\n", encoding="utf-8")
            run_git(target, "add", "tracked.txt")
            run_git(target, "commit", "-q", "-m", "concurrent")

        with patch.object(FixedGitWorktreeCommands, "add", add_then_commit):
            with self.assertRaisesRegex(WorkspaceError, "HEAD"):
                WorktreeManager(self.storage).create(
                    self.source, "lineage-1", "codex/task-lineage-1"
                )

        advanced = run_git(self.source, "rev-parse", "codex/task-lineage-1")
        self.assertNotEqual(advanced, run_git(self.source, "rev-parse", "HEAD"))

    def test_missing_registration_is_rejected_then_precisely_compensated(self) -> None:
        real_entries = FixedGitWorktreeCommands.entries
        calls = 0

        def hide_once(commands):
            nonlocal calls
            calls += 1
            entries = real_entries(commands)
            return () if calls == 1 else entries

        with patch.object(FixedGitWorktreeCommands, "entries", hide_once):
            with self.assertRaisesRegex(WorkspaceError, "registration"):
                WorktreeManager(self.storage).create(
                    self.source, "lineage-1", "codex/task-lineage-1"
                )

        self.assertNotIn("lineage-1", run_git(self.source, "worktree", "list", "--porcelain"))

    def test_add_failure_never_deletes_concurrent_same_tip_branch(self) -> None:
        branch = "codex/task-lineage-1"

        def race_then_fail(_commands, raced_branch, _target, head):
            run_git(self.source, "branch", raced_branch, head)
            raise WorkspaceError("injected add failure")

        with patch.object(FixedGitWorktreeCommands, "add", race_then_fail):
            with self.assertRaisesRegex(WorkspaceError, "injected add failure") as raised:
                WorktreeManager(self.storage).create(self.source, "lineage-1", branch)

        self.assertTrue(any("ownership" in item for item in raised.exception.cleanup_errors))
        self.assertEqual(run_git(self.source, "rev-parse", branch), run_git(self.source, "rev-parse", "HEAD"))


class WorktreePruneReviewTests(WorktreeTestCase):
    def test_prune_uses_exact_missing_path_and_preserves_late_unknown_stale(self) -> None:
        manager = WorktreeManager(self.storage)
        trusted = manager.create(self.source, "lineage-1", "codex/task-lineage-1")
        shutil.rmtree(trusted.root)
        late = self.root / "late-unknown"
        real_popen = subprocess.Popen
        injected = False

        def inject_before_destructive(argv, *args, **kwargs):
            nonlocal injected
            if not injected and "worktree" in argv and ("prune" in argv or "remove" in argv):
                injected = True
                _run_direct(real_popen, self.source, "worktree", "add", "-q", "-b", "late", str(late), "HEAD")
                shutil.rmtree(late)
            return real_popen(argv, *args, **kwargs)

        with patch("code_agent.workspace.git.subprocess.Popen", side_effect=inject_before_destructive) as invoked:
            self.assertEqual(manager.prune((trusted,)), (trusted.root,))

        destructive = [call.args[0] for call in invoked.call_args_list if "worktree" in call.args[0]]
        self.assertTrue(any(argv[-4:] == ["worktree", "remove", "--force", str(trusted.root)] for argv in destructive))
        listing = run_git(self.source, "worktree", "list", "--porcelain")
        self.assertIn(late.as_posix(), listing)


class WorktreeRemoveBooleanTests(WorktreeTestCase):
    def test_remove_requires_literal_true_and_boolean_active(self) -> None:
        manager = WorktreeManager(self.storage)
        created = manager.create(self.source, "lineage-1", "codex/task-lineage-1")

        for confirmed, active in ((1, False), ("yes", False), (True, 0), (True, None)):
            with self.subTest(confirmed=confirmed, active=active):
                with self.assertRaises(WorkspaceError):
                    manager.remove(created, confirmed=confirmed, active=active)
        self.assertTrue(created.root.exists())


def _run_direct(popen, root: Path, *arguments: str) -> None:
    process = popen(
        ["git", *arguments],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        env=os.environ.copy(),
    )
    stdout, stderr = process.communicate()
    if process.returncode:
        raise AssertionError(stderr.decode("utf-8", errors="replace") or stdout)


if __name__ == "__main__":
    unittest.main()
