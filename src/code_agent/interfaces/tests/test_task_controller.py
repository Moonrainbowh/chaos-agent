from __future__ import annotations

import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from code_agent.core.attachments import AttachmentRef
from code_agent.core.events import AgentEvent
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.sessions.errors import SessionStorageError
from code_agent.core.task import TaskStatus


class _IdleRunner:
    def run(self, user_input: str, **_: object) -> AsyncIterator[AgentEvent]:
        async def events() -> AsyncIterator[AgentEvent]:
            if False:
                yield AgentEvent  # pragma: no cover
        return events()


class ForegroundTaskControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_attachment_steering_persists_only_safe_message_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteSessionRepository(root / "sessions.sqlite3")
            controller = ForegroundTaskController(
                AgentController(_IdleRunner()), repository, root
            )
            task = await controller.start("inspect the screenshot")
            attachment = AttachmentRef(
                "a" * 64,
                "image/png",
                12,
                "screen.png",
                2,
                3,
            )

            await controller.steer(task.id, "", attachments=(attachment,))

            messages = await repository.load_messages(task.thread_id)
            self.assertEqual(messages[-1].attachments, (attachment,))
            self.assertEqual(messages[-1].content, "")

    async def test_failed_steering_transaction_leaves_no_orphan_or_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteSessionRepository(root / "sessions.sqlite3")
            controller = ForegroundTaskController(
                AgentController(_IdleRunner()), repository, root
            )
            task = await controller.start("inspect")
            attachment = AttachmentRef(
                "b" * 64, "image/png", 12, "screen.png", 2, 3
            )

            await repository._database.write(
                lambda connection: connection.execute(
                    "CREATE TRIGGER fail_steering BEFORE INSERT ON task_controls "
                    "BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
                )
            )
            with self.assertRaises(SessionStorageError):
                await controller.steer(task.id, "", attachments=(attachment,))

            self.assertEqual(await repository.load_messages(task.thread_id), ())
            self.assertEqual(await repository.consume_task_controls(task.id), ())
            await repository._database.write(
                lambda connection: connection.execute(
                    "DROP TRIGGER fail_steering"
                )
            )

            await controller.steer(task.id, "", attachments=(attachment,))

            messages = await repository.load_messages(task.thread_id)
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].attachments, (attachment,))
            self.assertEqual(
                await repository.consume_task_controls(task.id),
                ("apply attached user input",),
            )

    async def test_interrupted_task_does_not_block_a_new_foreground_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteSessionRepository(root / "sessions.sqlite3")
            controller = ForegroundTaskController(AgentController(_IdleRunner()), repository, root)
            interrupted = await controller.start("first task")
            await repository.transition_task(interrupted.id, TaskStatus.RUNNING)
            await repository.transition_task(interrupted.id, TaskStatus.INTERRUPTED)

            next_task = await controller.start("second task")

            self.assertNotEqual(next_task.id, interrupted.id)
            self.assertEqual(next_task.status, TaskStatus.CREATED)

    async def test_running_task_still_blocks_a_concurrent_foreground_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteSessionRepository(root / "sessions.sqlite3")
            controller = ForegroundTaskController(AgentController(_IdleRunner()), repository, root)
            running = await controller.start("first task")
            await repository.transition_task(running.id, TaskStatus.RUNNING)

            with self.assertRaisesRegex(RuntimeError, "already active"):
                await controller.start("second task")

    async def test_running_task_in_another_workspace_does_not_block_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first_root = base / "first"
            second_root = base / "second"
            first_root.mkdir()
            second_root.mkdir()
            repository = SQLiteSessionRepository(base / "sessions.sqlite3")
            first = ForegroundTaskController(AgentController(_IdleRunner()), repository, first_root)
            second = ForegroundTaskController(AgentController(_IdleRunner()), repository, second_root)
            running = await first.start("first task")
            await repository.transition_task(running.id, TaskStatus.RUNNING)

            next_task = await second.start("second task")

            self.assertEqual(next_task.contract.authorization.workspace_root, str(second_root.resolve()))

    async def test_pause_persists_task_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteSessionRepository(root / "sessions.sqlite3")
            controller = ForegroundTaskController(AgentController(_IdleRunner()), repository, root)
            task = await controller.start("repair tests")
            await controller.pause(task.id)
            self.assertEqual((await repository.load_task(task.id)).status, TaskStatus.PAUSED)
            self.assertEqual(len(await repository.list_checkpoints(task.thread_id)), 2)

    async def test_explicit_partial_acceptance_is_not_completed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteSessionRepository(root / "sessions.sqlite3")
            controller = ForegroundTaskController(AgentController(_IdleRunner()), repository, root)
            task = await controller.start("repair tests")
            await repository.transition_task(task.id, TaskStatus.RUNNING)
            await repository.transition_task(task.id, TaskStatus.VERIFYING)

            accepted = await controller.accept_partial(task.id, "user accepts known limitation")

            self.assertEqual(accepted.status, TaskStatus.ACCEPTED_PARTIAL)
            self.assertEqual((await repository.load_task(task.id)).status, TaskStatus.ACCEPTED_PARTIAL)
