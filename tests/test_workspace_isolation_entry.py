from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.core.task import TaskStatus
from code_agent.workspace.git import GitWorkspace
from code_agent_win.workspace_policy import isolated_tasks
from tests.agent_app_test_support import (
    _configured_application,
    _init_git_source,
    workspace_mode_scope,
)


class DeclaredIsolationEntryTests(unittest.IsolatedAsyncioTestCase):
    """`--isolated` and delegated work both reach a managed worktree."""

    async def test_the_explicit_scope_isolates_an_auto_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)
            seeding = patch.object(
                GitWorkspace,
                "changed_snapshot_paths",
                autospec=True,
                side_effect=GitWorkspace.changed_snapshot_paths,
            )

            with seeding as seed_dirty:
                with isolated_tasks("explicit"):
                    task = await application.foreground_tasks.start("isolated ask")

            task_root = Path(task.contract.authorization.workspace_root)
            self.assertNotEqual(task_root, root)
            self.assertTrue(task_root.is_dir())
            self.assertEqual(
                application.workspace_root_for(task.id), task_root
            )
            seed_dirty.assert_called_once()
            await application.aclose()

    async def test_the_background_scope_isolates_an_auto_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            with isolated_tasks("background"):
                task = await application.foreground_tasks.start("delegated ask")

            task_root = Path(task.contract.authorization.workspace_root)
            self.assertNotEqual(task_root, root)
            self.assertTrue(task_root.is_dir())
            await application.aclose()

    async def test_the_scope_ends_with_the_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            with isolated_tasks("explicit"):
                isolated = await application.foreground_tasks.start("isolated ask")
            await application.tui.sessions.transition_task(
                isolated.id, TaskStatus.FAILED, "test cleanup"
            )
            local = await application.foreground_tasks.start("plain ask")

            self.assertNotEqual(
                Path(isolated.contract.authorization.workspace_root), root
            )
            self.assertEqual(
                Path(local.contract.authorization.workspace_root), root
            )
            await application.aclose()


class WithheldIsolationTests(unittest.IsolatedAsyncioTestCase):
    """A declared reason this root cannot honor is refused, never downgraded."""

    async def test_direct_mode_refuses_a_declared_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            with workspace_mode_scope("direct"), isolated_tasks("explicit"):
                application = _configured_application(root)

                with self.assertRaisesRegex(RuntimeError, "direct-mode"):
                    await application.foreground_tasks.start("isolated ask")

                self.assertEqual(
                    await application.foreground_tasks._sessions.list_tasks(), ()
                )
                await application.aclose()

    async def test_a_non_repository_root_refuses_a_declared_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            container = Path(temporary).resolve()
            root = container / "plain"
            root.mkdir()
            (root / "note.py").write_text("print('plain')\n", encoding="utf-8")
            application = _configured_application(root)

            with isolated_tasks("background"):
                with self.assertRaisesRegex(
                    RuntimeError, "requires-git-repository"
                ):
                    await application.foreground_tasks.start("delegated ask")

            self.assertEqual(
                await application.foreground_tasks._sessions.list_tasks(), ()
            )
            await application.aclose()
