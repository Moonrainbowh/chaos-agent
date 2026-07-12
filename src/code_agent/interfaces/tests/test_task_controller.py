from __future__ import annotations

import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from code_agent.core.events import AgentEvent
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.core.task import TaskStatus


class _IdleRunner:
    def run(self, user_input: str, **_: object) -> AsyncIterator[AgentEvent]:
        async def events() -> AsyncIterator[AgentEvent]:
            if False:
                yield AgentEvent  # pragma: no cover
        return events()


class ForegroundTaskControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_persists_task_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteSessionRepository(root / "sessions.sqlite3")
            controller = ForegroundTaskController(AgentController(_IdleRunner()), repository, root)
            task = await controller.start("repair tests")
            await controller.pause(task.id)
            self.assertEqual((await repository.load_task(task.id)).status, TaskStatus.PAUSED)
