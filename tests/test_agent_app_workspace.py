from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.core.limits import EngineLimits
from code_agent.workspace.errors import WorkspaceError
from tests.agent_app_test_support import (
    _configured_application,
    _init_git_source,
    _tree_digest,
)


class ManagedWorkspaceApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_task_uses_managed_worktree_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            source_before = _tree_digest(root)
            application = _configured_application(root)

            task = await application.foreground_tasks.start("edit note.py")
            task_root = Path(task.contract.authorization.workspace_root)

            self.assertNotEqual(task_root, root)
            self.assertIn("managed-workspaces", str(task_root))
            self.assertEqual(_tree_digest(root), source_before)
            self.assertEqual(application.workspace_root_for(task.id), task_root)
            self.assertEqual(application.runtime_root_for(task.id), task_root)
            self.assertEqual(application.verification_root_for(task.id), task_root)
            await application.aclose()

    async def test_non_git_source_stays_available_without_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.py").write_text("print('hi')\n", encoding="utf-8")
            application = _configured_application(root)

            task = await application.foreground_tasks.start("inspect")

            self.assertEqual(
                Path(task.contract.authorization.workspace_root), root
            )
            with self.assertRaises(RuntimeError):
                await application.tui.checkpoints.list(task.id)
            await application.aclose()

    async def test_managed_worktree_failure_does_not_fallback_to_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            with patch.object(
                application.workspace_runtime,
                "prepare_task",
                side_effect=WorkspaceError("cannot create worktree"),
            ):
                with self.assertRaises(RuntimeError):
                    await application.foreground_tasks.start("edit safely")

            self.assertEqual(await application.foreground_tasks._sessions.list_tasks(), ())
            await application.aclose()

    async def test_active_managed_task_blocks_second_task_from_same_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            await application.foreground_tasks.start("first")

            with self.assertRaises(RuntimeError):
                await application.foreground_tasks.start("second")
            await application.aclose()

    async def test_startup_hydrates_persisted_worktree_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            first = _configured_application(root)
            task = await first.foreground_tasks.start("edit note.py")
            task_root = Path(task.contract.authorization.workspace_root)
            await first.aclose()

            restarted = _configured_application(root)
            await restarted.startup()

            self.assertEqual(restarted.workspace_root_for(task.id), task_root)
            self.assertEqual(
                restarted.workspace_runtime.root_for_thread(task.thread_id),
                task_root,
            )
            await restarted.aclose()

    async def test_session_rewind_binds_replacement_task_to_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)
            task = await application.foreground_tasks.start("rewind session")
            task_root = Path(task.contract.authorization.workspace_root)
            await application.tui.sessions.get_or_create_task_budget(
                task.thread_id, "test", EngineLimits()
            )
            checkpoint = await application.tui.checkpoints.create(
                task.id, "rewindable"
            )
            preview = await application.tui.checkpoints.preview_rewind(
                task.id, checkpoint.id, "session"
            )

            result = await application.tui.checkpoints.execute_rewind(
                preview, confirmed=True
            )

            self.assertIsNotNone(result.replacement_task_id)
            self.assertEqual(
                application.workspace_root_for(result.replacement_task_id),
                task_root,
            )
            replacement = await application.tui.sessions.load_task(
                result.replacement_task_id
            )
            self.assertEqual(
                application.workspace_runtime.root_for_thread(
                    replacement.thread_id
                ),
                task_root,
            )
            await application.aclose()


if __name__ == "__main__":
    unittest.main()
