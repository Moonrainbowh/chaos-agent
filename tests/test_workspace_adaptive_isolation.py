"""Adaptive isolation and local checkpoint behaviour.

Two `auto` decisions are covered here: a task that finds another writer on its
root is isolated instead of refused, and a local task still owns a
worktree-free workspace lineage so it can checkpoint and rewind on demand.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.sessions.errors import SessionNotFound
from tests.agent_app_test_support import (
    _assert_no_worktree,
    _configured_application,
    _init_git_source,
    workspace_mode_scope,
)


class ConcurrentWriterIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_redirects_a_second_writer_into_a_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            first = await application.foreground_tasks.start("first task")
            second = await application.foreground_tasks.start("second task")

            self.assertEqual(
                Path(first.contract.authorization.workspace_root), root
            )
            second_root = Path(second.contract.authorization.workspace_root)
            self.assertNotEqual(second_root, root)
            self.assertIn("managed-workspaces", str(second_root))
            await application.aclose()

    async def test_direct_refuses_a_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            with workspace_mode_scope("direct"):
                application = _configured_application(root)

                await application.foreground_tasks.start("first task")
                with self.assertRaisesRegex(RuntimeError, "already active"):
                    await application.foreground_tasks.start("second task")

                _assert_no_worktree(self, application, root)
                await application.aclose()

    async def test_auto_refuses_a_second_writer_without_a_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "note.py").write_text("print('plain')\n", encoding="utf-8")
            application = _configured_application(root)

            await application.foreground_tasks.start("first task")
            with self.assertRaisesRegex(RuntimeError, "already active"):
                await application.foreground_tasks.start("second task")

            await application.aclose()

    async def test_managed_gives_each_task_its_own_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            with workspace_mode_scope("managed"):
                application = _configured_application(root)

                first = await application.foreground_tasks.start("first task")
                second = await application.foreground_tasks.start("second task")

                first_root = Path(first.contract.authorization.workspace_root)
                second_root = Path(second.contract.authorization.workspace_root)
                self.assertNotEqual(first_root, second_root)
                self.assertNotEqual(first_root, root)
                self.assertNotEqual(second_root, root)
                await application.aclose()


class LocalCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_task_owns_a_worktree_free_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            task = await application.foreground_tasks.start("edit note.py")
            lineage = await application.tui.sessions.load_lineage_for_task(task.id)

            self.assertEqual(Path(lineage.source_root), root)
            self.assertEqual(Path(lineage.worktree_root), root)
            self.assertTrue(lineage.head_commit)
            self.assertEqual(lineage.owner_task_id, task.id)
            _assert_no_worktree(self, application, root)
            await application.aclose()

    async def test_local_boundaries_stay_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            task = await application.foreground_tasks.start("edit note.py")
            created = await _metadata_checkpoint(
                application, task.id, "task-created"
            )

            self.assertEqual(created.metadata["task_id"], task.id)
            with self.assertRaises(SessionNotFound):
                await application.tui.sessions.load_workspace_snapshot(created.id)
            await application.aclose()

    async def test_new_local_task_after_a_paused_task_starts_without_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            first = await application.foreground_tasks.start("first task")
            await application.foreground_tasks.pause(first.id)

            second = await application.foreground_tasks.start("second task")

            with self.assertRaises(SessionNotFound):
                await application.tui.sessions.load_lineage_for_task(second.id)
            created = await _metadata_checkpoint(
                application, second.id, "task-created"
            )
            self.assertEqual(created.metadata["task_id"], second.id)
            await application.aclose()

    async def test_local_task_rewinds_the_source_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            task = await application.foreground_tasks.start("restore local code")
            # An explicit checkpoint is what makes a local task rewindable.
            checkpoint = await application.tui.checkpoints.create(
                task.id, "before edit"
            )
            self.assertIsNotNone(
                await application.tui.sessions.load_workspace_snapshot(
                    checkpoint.id
                )
            )
            (root / "note.py").write_text("print('changed')\n", encoding="utf-8")
            (root / "added.py").write_text("print('remove me')\n", encoding="utf-8")

            preview = await application.tui.checkpoints.preview_rewind(
                task.id, checkpoint.id, "code"
            )
            result = await application.tui.checkpoints.execute_rewind(
                preview, confirmed=True
            )

            self.assertIsNone(result.replacement_task_id)
            self.assertEqual(
                (root / "note.py").read_text(encoding="utf-8"),
                "print('dirty')\n",
            )
            self.assertFalse((root / "added.py").exists())
            _assert_no_worktree(self, application, root)
            await application.aclose()


async def _metadata_checkpoint(application, task_id: str, label: str):
    """Read one raw boundary record, metadata-only ones included.

    ``checkpoints.list`` returns only records that carry a workspace cursor,
    so a metadata-only boundary is visible only through the session store.
    """
    task = await application.tui.sessions.load_task(task_id)
    return next(
        item
        for item in await application.tui.sessions.list_checkpoints(task.thread_id)
        if item.label == label
    )


if __name__ == "__main__":
    unittest.main()
