from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from code_agent.core.cancellation import CancellationError
from code_agent.core.task import TaskStatus
from tests.agent_app_test_support import _configured_application, _init_git_source


class _BlockingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, *args: object, cancellation: object, **kwargs: object):
        self.started.set()
        await cancellation.wait_async()
        raise CancellationError(cancellation.reason or "cancelled")
        yield None


async def _checkpoint_with_label(application, task_id: str, label: str):
    return next(
        item
        for item in await application.tui.checkpoints.list(task_id)
        if item.label == label
    )


async def _start_blocked_rewind(application, task_id: str, preview: object):
    runner = _BlockingRunner()
    application.controller.replace_runner(runner)
    stream = application.foreground_tasks.events(task_id)
    draining = asyncio.create_task(_drain(stream))
    await asyncio.wait_for(runner.started.wait(), 10)
    lifecycle = application.foreground_tasks._checkpoint_lifecycle
    original_capture = lifecycle.capture_settled
    capture_started = asyncio.Event()
    allow_capture = asyncio.Event()

    async def delayed_capture(captured_task_id: str, serial: int) -> None:
        capture_started.set()
        await allow_capture.wait()
        await original_capture(captured_task_id, serial)

    lifecycle.capture_settled = delayed_capture
    rewinding = asyncio.create_task(
        application.tui.checkpoints.execute_rewind(preview, confirmed=True)
    )
    await asyncio.wait_for(capture_started.wait(), 10)
    return rewinding, draining, allow_capture


class WorkspaceCheckpointApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle_boundaries_publish_real_workspace_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            task = await application.foreground_tasks.start("edit note.py")
            task_root = Path(task.contract.authorization.workspace_root)
            created = await _checkpoint_with_label(application, task.id, "task-created")
            (task_root / "note.py").write_text(
                "print('checkpointed')\n", encoding="utf-8"
            )
            await application.foreground_tasks.pause(task.id, "test boundary")
            paused = await _checkpoint_with_label(application, task.id, "task-paused")

            self.assertIsNotNone(
                await application.tui.sessions.load_workspace_snapshot(created.id)
            )
            self.assertIsNotNone(
                await application.tui.sessions.load_workspace_snapshot(paused.id)
            )
            self.assertNotEqual(
                created.metadata["inventory_digest"],
                paused.metadata["inventory_digest"],
            )
            await application.aclose()

    async def test_active_session_rewind_waits_for_settled_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            task = await application.foreground_tasks.start("active rewind")
            task_root = Path(task.contract.authorization.workspace_root)
            checkpoint = await _checkpoint_with_label(application, task.id, "task-created")
            checkpointed_text = (task_root / "note.py").read_text(
                encoding="utf-8"
            )
            (task_root / "note.py").write_text(
                "print('active change')\n", encoding="utf-8"
            )
            preview = await application.tui.checkpoints.preview_rewind(
                task.id, checkpoint.id, "code_and_session"
            )

            rewinding, draining, allow_capture = await _start_blocked_rewind(
                application, task.id, preview
            )
            await asyncio.sleep(0)
            self.assertFalse(rewinding.done())

            allow_capture.set()
            result = await asyncio.wait_for(rewinding, 20)
            await asyncio.wait_for(draining, 10)

            self.assertIsNotNone(result.replacement_task_id)
            self.assertEqual(
                (task_root / "note.py").read_text(encoding="utf-8"),
                checkpointed_text,
            )
            replacement = await application.tui.sessions.load_task(
                result.replacement_task_id
            )
            lineage = await application.tui.sessions.load_lineage_for_task(
                replacement.id
            )
            self.assertEqual(lineage.owner_task_id, replacement.id)
            self.assertIn(
                "task-paused",
                {
                    item.label
                    for item in await application.tui.checkpoints.list(task.id)
                },
            )
            await application.aclose()

    async def test_code_rewind_restores_managed_worktree_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            task = await application.foreground_tasks.start("restore code")
            task_root = Path(task.contract.authorization.workspace_root)
            checkpoint = await _checkpoint_with_label(
                application, task.id, "task-created"
            )
            checkpointed_text = (task_root / "note.py").read_text(
                encoding="utf-8"
            )
            (task_root / "note.py").write_text(
                "print('changed')\n", encoding="utf-8"
            )
            (task_root / "added.py").write_text(
                "print('remove me')\n", encoding="utf-8"
            )

            preview = await application.tui.checkpoints.preview_rewind(
                task.id, checkpoint.id, "code"
            )
            result = await application.tui.checkpoints.execute_rewind(
                preview, confirmed=True
            )

            self.assertIsNone(result.replacement_task_id)
            self.assertEqual(
                (task_root / "note.py").read_text(encoding="utf-8"),
                checkpointed_text,
            )
            self.assertFalse((task_root / "added.py").exists())
            self.assertEqual(
                (await application.tui.sessions.load_task(task.id)).status,
                TaskStatus.PAUSED,
            )
            self.assertIn(
                "pre-rewind",
                {
                    item.label
                    for item in await application.tui.checkpoints.list(task.id)
                },
            )
            await application.aclose()


async def _drain(stream: object) -> list[object]:
    return [event async for event in stream]


if __name__ == "__main__":
    unittest.main()
