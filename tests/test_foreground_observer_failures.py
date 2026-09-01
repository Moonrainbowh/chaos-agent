from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from code_agent.core.task import TaskStatus
from tests.agent_app_test_support import _configured_application, _init_git_source


class ForegroundObserverFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_failure_interrupts_claimed_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)

            with patch.object(
                application.foreground_tasks.workflows,
                "observe",
                new=AsyncMock(side_effect=RuntimeError("workflow save failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "workflow save failed"):
                    await application.foreground_tasks.start("edit safely")

            await self._assert_failure_released(application)
            await application.aclose()

    async def test_plugin_failure_interrupts_claimed_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)
            plugin_events = AsyncMock()
            plugin_events.observe.side_effect = RuntimeError("plugin event failed")
            application.foreground_tasks._plugin_events = plugin_events

            with self.assertRaisesRegex(RuntimeError, "plugin event failed"):
                await application.foreground_tasks.start("edit safely")

            application.foreground_tasks._plugin_events = None
            await self._assert_failure_released(application)
            await application.aclose()

    async def test_repeated_cancel_settles_task_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)
            sessions = application.foreground_tasks._sessions
            observer_entered = asyncio.Event()

            async def block_observer(*_arguments) -> None:
                observer_entered.set()
                await asyncio.Event().wait()

            plugin_events = AsyncMock()
            plugin_events.observe.side_effect = block_observer
            application.foreground_tasks._plugin_events = plugin_events
            start = asyncio.create_task(
                application.foreground_tasks.start("cancel observer")
            )
            await observer_entered.wait()
            load_entered = asyncio.Event()
            release_load = asyncio.Event()
            real_load = sessions.load_task

            async def blocked_load(task_id: str):
                load_entered.set()
                await release_load.wait()
                return await real_load(task_id)

            with patch.object(sessions, "load_task", new=blocked_load):
                start.cancel()
                await load_entered.wait()
                start.cancel()
                release_load.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(start, 3)

            application.foreground_tasks._plugin_events = None
            await self._assert_failure_released(application)
            await application.aclose()

    async def test_transient_interruption_failure_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)
            sessions = application.foreground_tasks._sessions
            plugin_events = AsyncMock()
            plugin_events.observe.side_effect = RuntimeError("plugin event failed")
            application.foreground_tasks._plugin_events = plugin_events
            real_transition = sessions.transition_task
            attempts = 0

            async def fail_once(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("transition temporarily unavailable")
                return await real_transition(*args, **kwargs)

            with patch.object(sessions, "transition_task", new=fail_once):
                with self.assertRaisesRegex(RuntimeError, "plugin event failed"):
                    await application.foreground_tasks.start("edit safely")

            self.assertEqual(attempts, 2)
            application.foreground_tasks._plugin_events = None
            await self._assert_failure_released(application)
            await application.aclose()

    async def test_persistent_interruption_failure_notes_original_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _init_git_source(root)
            application = _configured_application(root)
            sessions = application.foreground_tasks._sessions
            plugin_events = AsyncMock()
            plugin_events.observe.side_effect = RuntimeError("plugin event failed")
            application.foreground_tasks._plugin_events = plugin_events
            transition = AsyncMock(
                side_effect=RuntimeError("transition unavailable")
            )

            with patch.object(
                sessions,
                "transition_task",
                new=transition,
            ):
                with self.assertRaisesRegex(RuntimeError, "plugin event failed") as raised:
                    await application.foreground_tasks.start("edit safely")

            self.assertEqual(transition.await_count, 3)
            notes = getattr(raised.exception, "__notes__", ())
            self.assertTrue(any("transition unavailable" in note for note in notes))
            failed = (await sessions.list_tasks(include_terminal=True))[0]
            self.assertEqual(failed.status, TaskStatus.CREATED)
            await application.aclose()

    async def _assert_failure_released(self, application) -> None:
        sessions = application.foreground_tasks._sessions
        failed = (await sessions.list_tasks(include_terminal=True))[0]
        lineage = await sessions.load_lineage_for_task(failed.id)

        self.assertEqual(failed.status, TaskStatus.INTERRUPTED)
        self.assertEqual(failed.stop_reason, "task initialization observer failed")
        self.assertTrue(Path(lineage.worktree_root).exists())
        self.assertEqual(application.workspace_runtime._prepared, {})

        replacement = await application.foreground_tasks.start("retry")
        self.assertEqual(replacement.status, TaskStatus.CREATED)


if __name__ == "__main__":
    unittest.main()
