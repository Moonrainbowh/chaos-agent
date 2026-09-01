from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.errors import WorkspaceError  # noqa: E402
from code_agent.workspace._git_worktrees import FixedGitWorktreeCommands  # noqa: E402
from code_agent.workspace.git import (  # noqa: E402
    GitOutputLimitError,
    GitTimeoutError,
    GitWorkspace,
)
from code_agent.workspace.tests.test_git_limits import HangingProcess  # noqa: E402
from code_agent.workspace.tests.test_worktrees import WorktreeTestCase, run_git  # noqa: E402
from code_agent.workspace.worktrees import WorktreeManager  # noqa: E402


class WorktreeCreationSecurityTests(WorktreeTestCase):
    @unittest.skipUnless(os.name == "nt", "Git worktree limit is Windows-only")
    def test_default_keeps_git_worktree_headroom_below_win32_limit(self) -> None:
        self.assertEqual(WorktreeManager(self.storage).max_path_chars, 215)

    def test_manager_validates_bounded_git_limits_at_construction(self) -> None:
        for arguments in (
            {"max_output_bytes": 0},
            {"max_output_bytes": True},
            {"timeout_s": 0},
            {"timeout_s": float("inf")},
            {"timeout_s": True},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises((TypeError, ValueError)):
                    WorktreeManager(self.storage, **arguments)

    def test_manager_has_no_git_executable_or_flag_injection(self) -> None:
        with self.assertRaises(TypeError):
            WorktreeManager(self.storage, git_executable="other-git")
        with self.assertRaises(TypeError):
            WorktreeManager(self.storage, git_flags=("--force",))

    def test_manager_git_calls_keep_the_configured_timeout(self) -> None:
        process = HangingProcess()
        manager = WorktreeManager(self.storage, timeout_s=0.01)

        with patch("code_agent.workspace.git.subprocess.Popen", return_value=process):
            with self.assertRaises(GitTimeoutError):
                manager.identify(self.source)

        self.assertTrue(process.killed)
        self.assertTrue(process.wait_timeouts)
        self.assertLessEqual(process.wait_timeouts[0], 0.01)

    def test_manager_git_calls_keep_the_configured_output_limit(self) -> None:
        with self.assertRaises(GitOutputLimitError):
            WorktreeManager(self.storage, max_output_bytes=4).identify(self.source)
    def test_exact_branch_character_class_accepts_repeated_hyphens(self) -> None:
        created = WorktreeManager(self.storage).create(
            self.source, "lineage--1", "codex/task-lineage--1"
        )

        self.assertEqual(created.lineage_id, "lineage--1")

    def test_names_reject_absolute_parent_and_unbound_values(self) -> None:
        invalid = (
            ("../escape", "codex/task-escape"),
            ("/absolute", "codex/task-absolute"),
            ("Lineage", "codex/task-Lineage"),
            ("lineage-1", "codex/task-other"),
            ("lineage-1", "codex/task-lineage-1/extra"),
            ("lineage-1", "--force"),
        )

        for lineage, branch in invalid:
            with self.subTest(lineage=lineage, branch=branch):
                with self.assertRaises(WorkspaceError):
                    WorktreeManager(self.storage).create(self.source, lineage, branch)

        self.assertEqual(tuple(self.storage.iterdir()), ())

    def test_existing_target_directory_or_file_is_never_reused(self) -> None:
        manager = WorktreeManager(self.storage)
        repository_root = self.storage / manager.identify(self.source).repository_id
        repository_root.mkdir()
        (repository_root / "occupied-dir").mkdir()
        (repository_root / "occupied-file").write_text("owned", encoding="utf-8")

        for lineage in ("occupied-dir", "occupied-file"):
            with self.subTest(lineage=lineage):
                with self.assertRaisesRegex(WorkspaceError, "already exists"):
                    manager.create(self.source, lineage, f"codex/task-{lineage}")

    def test_repository_storage_component_cannot_be_a_link(self) -> None:
        manager = WorktreeManager(self.storage)
        repository_id = manager.identify(self.source).repository_id
        outside = self.root / "outside"
        outside.mkdir()
        linked = self.storage / repository_id
        try:
            os.symlink(outside, linked, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("directory symlinks are unavailable")
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(linked), str(outside)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            if result.returncode:
                self.skipTest("directory reparse points are unavailable")

        with self.assertRaises(WorkspaceError):
            manager.create(self.source, "lineage-1", "codex/task-lineage-1")
        self.assertEqual(tuple(outside.iterdir()), ())

    def test_storage_root_itself_cannot_be_a_link(self) -> None:
        outside = self.root / "outside-storage"
        outside.mkdir()
        linked = self.root / "linked-storage"
        try:
            os.symlink(outside, linked, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("directory symlinks are unavailable")
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(linked), str(outside)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            if result.returncode:
                self.skipTest("directory reparse points are unavailable")

        with self.assertRaisesRegex(WorkspaceError, "storage root"):
            WorktreeManager(linked)

    def test_path_length_limit_is_checked_before_creating_parent(self) -> None:
        manager = WorktreeManager(self.storage, max_path_chars=len(str(self.storage)) + 20)

        with self.assertRaisesRegex(WorkspaceError, "path length"):
            manager.create(
                self.source,
                "lineage-with-a-long-name",
                "codex/task-lineage-with-a-long-name",
            )

        self.assertEqual(tuple(self.storage.iterdir()), ())

    @unittest.skipUnless(os.name == "nt", "Windows path units are Windows-only")
    def test_custom_path_limit_counts_utf16_code_units(self) -> None:
        storage = self.root / ("\U0001f600" * 10)
        storage.mkdir()
        probe = WorktreeManager(storage)
        repository_id = probe.identify(self.source).repository_id
        proposed = storage / repository_id / "lineage-1"
        code_points = len(str(proposed))
        utf16_units = len(str(proposed).encode("utf-16-le")) // 2
        self.assertGreater(utf16_units, code_points)
        manager = WorktreeManager(storage, max_path_chars=code_points)

        with patch.object(
            manager,
            "_create_locked",
            side_effect=AssertionError("path limit was bypassed"),
        ):
            with self.assertRaisesRegex(WorkspaceError, "path length"):
                manager.create(
                    self.source, "lineage-1", "codex/task-lineage-1"
                )

    @unittest.skipUnless(os.name == "nt", "Git worktree limit is Windows-only")
    def test_custom_limit_cannot_relax_the_git_hard_limit(self) -> None:
        repository_id = WorktreeManager(self.storage).identify(
            self.source
        ).repository_id
        lineage = "lineage-1"
        suffix_units = 1 + len(repository_id) + 1 + len(lineage)
        storage_units = 216 - suffix_units
        component_units = storage_units - len(str(self.root)) - 1
        storage = self.root / ("s" * component_units)
        storage.mkdir()
        target = storage / repository_id / lineage
        self.assertEqual(len(str(target).encode("utf-16-le")) // 2, 216)
        manager = WorktreeManager(storage, max_path_chars=1_000)

        with patch.object(
            manager,
            "_create_locked",
            side_effect=AssertionError("Git hard limit was bypassed"),
        ):
            with self.assertRaisesRegex(WorkspaceError, "path length"):
                manager.create(
                    self.source, lineage, f"codex/task-{lineage}"
                )

        self.assertEqual(tuple(storage.iterdir()), ())

    def test_git_creation_argv_is_fixed_and_has_no_model_flags(self) -> None:
        real_popen = subprocess.Popen
        with patch("code_agent.workspace.git.subprocess.Popen", wraps=real_popen) as invoked:
            created = WorktreeManager(self.storage).create(
                self.source, "lineage-1", "codex/task-lineage-1"
            )

        calls = [
            call.args[0]
            for call in invoked.call_args_list
            if "worktree" in call.args[0] and "add" in call.args[0]
        ]
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertEqual(
            argv[-6:],
            ["worktree", "add", "-b", created.branch_name, str(created.root), created.head_commit],
        )
        self.assertNotIn("--force", argv)

    def test_postcheck_failure_compensates_registered_worktree_and_branch(self) -> None:
        real_status = GitWorkspace.status_porcelain
        calls = 0

        def changing_status(git: GitWorkspace) -> str:
            nonlocal calls
            calls += 1
            value = real_status(git)
            return value if calls == 1 else value + "?? changed\n"

        with patch.object(GitWorkspace, "status_porcelain", changing_status):
            with self.assertRaisesRegex(WorkspaceError, "changed during"):
                WorktreeManager(self.storage).create(
                    self.source, "lineage-1", "codex/task-lineage-1"
                )

        repository_id = WorktreeManager(self.storage).identify(self.source).repository_id
        self.assertFalse((self.storage / repository_id / "lineage-1").exists())
        self.assertEqual(
            subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", "refs/heads/codex/task-lineage-1"],
                cwd=self.source,
                shell=False,
                env=os.environ.copy(),
            ).returncode,
            1,
        )

    def test_failed_unregistered_creation_does_not_delete_unknown_content(self) -> None:
        manager = WorktreeManager(self.storage)
        repository_id = manager.identify(self.source).repository_id
        target = self.storage / repository_id / "lineage-1"

        def fail_with_unknown(_git: GitWorkspace, _branch: str, path: Path, _head: str) -> None:
            path.mkdir()
            (path / "owner.txt").write_text("unknown", encoding="utf-8")
            raise WorkspaceError("injected creation failure")

        with patch.object(FixedGitWorktreeCommands, "add", fail_with_unknown):
            with self.assertRaisesRegex(WorkspaceError, "injected"):
                manager.create(self.source, "lineage-1", "codex/task-lineage-1")

        self.assertEqual((target / "owner.txt").read_text(encoding="utf-8"), "unknown")

    def test_compensation_failure_does_not_hide_creation_error(self) -> None:
        manager = WorktreeManager(self.storage)

        with patch.object(
            FixedGitWorktreeCommands,
            "add",
            side_effect=WorkspaceError("primary creation failure"),
        ), patch.object(
            FixedGitWorktreeCommands,
            "branch_tip",
            side_effect=WorkspaceError("cleanup failure"),
        ):
            with self.assertRaisesRegex(WorkspaceError, "primary creation failure"):
                manager.create(self.source, "lineage-1", "codex/task-lineage-1")


if __name__ == "__main__":
    unittest.main()
