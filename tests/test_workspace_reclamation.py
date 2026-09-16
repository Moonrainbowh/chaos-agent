from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from code_agent.core.task import TaskStatus
from code_agent.workspace.git import GitWorkspace
from code_agent.workspace.worktrees import WorktreeManager
from code_agent_win.workspace_models import task_branch_name
from tests.agent_app_test_support import (
    _configured_application,
    _git,
    workspace_mode_scope,
)


def _clean_git_source(root: Path) -> None:
    """Commit everything, so a managed worktree of this root starts clean.

    ``note.py`` is written as LF bytes on purpose. ``Path.write_text`` turns
    ``\\n`` into CRLF on Windows, while the product runs Git with the user's
    global config disabled, so a CRLF checkout reads as modified there.
    """
    (root / "note.py").write_bytes(b"print('source')\n")
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "note.py")
    _git(root, "commit", "-m", "initial")


def _worktrees_root(application) -> Path:
    return application.workspace_runtime.storage_root / "worktrees"


def _branch_exists(root: Path, branch: str) -> bool:
    result = GitWorkspace(root)._invoke(
        "test_branch", ("show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    )
    return result.returncode == 0


async def _start_isolated_task(application, prompt: str = "isolated ask"):
    task = await application.foreground_tasks.start(prompt)
    root = Path(task.contract.authorization.workspace_root)
    return task, root


class OrphanWorktreeTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_orphan_worktree_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _clean_git_source(root)
            application = _configured_application(root)
            manager = WorktreeManager(_worktrees_root(application))
            lineage_id = uuid.uuid4().hex
            branch = task_branch_name(lineage_id)
            orphan = manager.create_unclaimed(root, lineage_id, branch).worktree.root
            self.assertTrue(orphan.is_dir())

            report = await application.workspace_runtime.reclaim_workspaces()

            self.assertEqual(report.reclaimed_roots, (orphan,))
            self.assertIn("no lineage", report.reclaimed[0].reason)
            self.assertFalse(orphan.exists())
            self.assertFalse(_branch_exists(root, branch))
            await application.aclose()

    async def test_an_orphan_worktree_is_gone_after_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _clean_git_source(root)
            first = _configured_application(root)
            manager = WorktreeManager(_worktrees_root(first))
            lineage_id = uuid.uuid4().hex
            orphan = manager.create_unclaimed(
                root, lineage_id, task_branch_name(lineage_id)
            ).worktree.root
            await first.aclose()

            second = _configured_application(root)
            await second.startup()

            self.assertFalse(orphan.exists())
            await second.aclose()

    async def test_an_untouched_storage_root_reports_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _clean_git_source(root)
            application = _configured_application(root)

            report = await application.workspace_runtime.reclaim_workspaces()

            self.assertEqual(report.reclaimed, ())
            self.assertEqual(report.retained, ())
            await application.aclose()


class FinishedTaskWorktreeTests(unittest.IsolatedAsyncioTestCase):
    async def _finished_task(self, application) -> Path:
        task, worktree_root = await _start_isolated_task(application)
        self.assertNotEqual(worktree_root, Path(application.workspace_root))
        await application.tui.sessions.transition_task(
            task.id, TaskStatus.FAILED, "test cleanup"
        )
        return worktree_root

    async def test_a_default_pass_keeps_a_rewindable_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _clean_git_source(root)
            with workspace_mode_scope("managed"):
                application = _configured_application(root)
                worktree_root = await self._finished_task(application)

                report = await application.workspace_runtime.reclaim_workspaces()

            self.assertEqual(report.reclaimed_roots, ())
            self.assertIn("rewindable", report.retained[0].reason)
            self.assertTrue(worktree_root.is_dir())
            await application.aclose()

    async def test_an_explicit_pass_retires_a_rewindable_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _clean_git_source(root)
            with workspace_mode_scope("managed"):
                application = _configured_application(root)
                worktree_root = await self._finished_task(application)

                report = await application.workspace_runtime.reclaim_workspaces(
                    rewindable=True
                )

            self.assertEqual(
                report.reclaimed_roots, (worktree_root,), msg=report.retained
            )
            self.assertFalse(worktree_root.exists())
            await application.aclose()

    async def test_an_active_task_keeps_its_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _clean_git_source(root)
            with workspace_mode_scope("managed"):
                application = _configured_application(root)
                _, worktree_root = await _start_isolated_task(application)

                report = await application.workspace_runtime.reclaim_workspaces(
                    rewindable=True
                )

            self.assertEqual(report.reclaimed_roots, ())
            self.assertIn("the owning task is created", report.retained[0].reason)
            self.assertTrue(worktree_root.is_dir())
            await application.aclose()

    async def test_a_task_branch_with_its_own_commit_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _clean_git_source(root)
            with workspace_mode_scope("managed"):
                application = _configured_application(root)
                worktree_root = await self._finished_task(application)
                (worktree_root / "result.py").write_bytes(b"print('result')\n")
                _git(worktree_root, "add", "-A")
                _git(worktree_root, "commit", "-m", "task work")

                report = await application.workspace_runtime.reclaim_workspaces(
                    rewindable=True
                )

            self.assertEqual(report.reclaimed_roots, ())
            self.assertIn("commits of its own", report.retained[0].reason)
            self.assertTrue(worktree_root.is_dir())
            await application.aclose()

    async def test_a_worktree_with_uncommitted_edits_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _clean_git_source(root)
            with workspace_mode_scope("managed"):
                application = _configured_application(root)
                worktree_root = await self._finished_task(application)
                (worktree_root / "scratch.txt").write_text(
                    "wip\n", encoding="utf-8"
                )

                report = await application.workspace_runtime.reclaim_workspaces(
                    rewindable=True
                )

            self.assertEqual(report.reclaimed_roots, ())
            self.assertIn("dirty", report.retained[0].reason)
            self.assertTrue(worktree_root.is_dir())
            await application.aclose()

    async def test_startup_survives_a_retired_task_worktree(self) -> None:
        """A retired worktree leaves a lineage whose directory is gone.

        Every later startup re-hydrates that lineage, so startup must treat a
        missing task root as "nothing to recover" rather than failing.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _clean_git_source(root)
            with workspace_mode_scope("managed"):
                first = _configured_application(root)
                worktree_root = await self._finished_task(first)
                report = await first.workspace_runtime.reclaim_workspaces(
                    rewindable=True
                )
                self.assertEqual(report.reclaimed_roots, (worktree_root,))
                await first.aclose()

                second = _configured_application(root)
                await second.startup()

            self.assertFalse(worktree_root.exists())
            await second.aclose()
