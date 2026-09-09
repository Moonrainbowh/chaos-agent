from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from code_agent.core.events import AgentEvent, EventKind
from code_agent.interfaces.tests.test_command_navigation import make_app


class DelayedTasks:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cleaned = False
        self.calls = 0

    async def start(self, prompt):
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
        finally:
            self.cleaned = True
        return SimpleNamespace(id="task-1")

    async def events(self, task_id, prompt):
        yield AgentEvent(EventKind.TASK_STATUS_CHANGED, {"task_id": task_id, "status": "completed"})
        yield AgentEvent(EventKind.COMPLETED, {})


class StartupResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_preparation_animates_and_keyboard_remains_responsive(self):
        tasks = DelayedTasks()
        app = make_app(tasks=tasks)
        output = []
        animated = asyncio.Event()

        def write(value):
            output.append(value)
            if app._spinner_index > 0:
                animated.set()

        app._write = write
        self.assertTrue(await app.submit("hello"))
        await asyncio.wait_for(tasks.started.wait(), 1)
        await asyncio.wait_for(animated.wait(), 1)
        self.assertEqual(app.state.status, "preparing_workspace")
        await app.handle_key("x")
        self.assertEqual(app.input.text, "x")
        tasks.release.set()
        await app.wait_idle()
        self.assertEqual(app.state.status, "completed")
        self.assertIsNone(app.active_task_id)
        self.assertNotIn("[queue]", output[-1])
        self.assertIsNone(app._run_started_at)

    async def test_escape_during_preparation_waits_for_cleanup_and_keeps_input(self):
        tasks = DelayedTasks()
        app = make_app(tasks=tasks)
        await app.submit("inspect files")
        await asyncio.wait_for(tasks.started.wait(), 1)
        await app.handle_key("\x1b")
        self.assertFalse(app.composer_expanded)
        self.assertFalse(tasks.cleaned)
        await asyncio.wait_for(app.handle_key("\x1b"), 1)
        await asyncio.wait_for(app.wait_idle(), 1)
        self.assertTrue(tasks.cleaned)
        self.assertEqual(app.state.status, "paused")
        self.assertEqual(app.input.text, "inspect files")

    async def test_preparation_prevents_duplicate_task_and_retains_second_input(self):
        tasks = DelayedTasks()
        app = make_app(tasks=tasks)
        await app.submit("first")
        self.assertFalse(await app.submit("second"))
        self.assertEqual(tasks.calls, 1)
        self.assertEqual(app.input.text, "second")
        tasks.release.set()
        await app.wait_idle()
