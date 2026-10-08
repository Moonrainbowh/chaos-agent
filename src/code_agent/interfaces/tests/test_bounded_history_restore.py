"""Default history restore pages journals and projects current durable delivery."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import Message
from code_agent.core.task import TaskContract, TaskAuthorization, TaskStatus
from code_agent.core.task_state import TaskState
from code_agent.interfaces.history import load_thread_history
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.interfaces.terminal_state import TerminalState
from code_agent.sessions.repository import SQLiteSessionRepository


class BoundedHistoryRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_partial_result_survives_later_telemetry_without_full_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "explicit-history.sqlite3"
            assert path.resolve().is_relative_to(root)
            sessions = SQLiteSessionRepository(path)
            thread = await sessions.create_thread()
            task = await sessions.create_task(thread, TaskContract("Inspect code",
                TaskAuthorization.local_workspace(str(root))))
            await sessions.save_task_state(thread, TaskState.empty())
            await sessions.transition_task(task.id, TaskStatus.RUNNING)
            await sessions.transition_task(task.id, TaskStatus.WAITING_DECISION)
            controller = ForegroundTaskController(None, sessions, root)
            await controller.accept_partial(task.id)
            accepted_result = await controller.result(task.id)
            for index in range(250):
                await sessions.append_message(thread, Message("user", f"prior {index}"))
                await sessions.append_event(thread, AgentEvent(EventKind.MODEL_EVENT,
                    {"telemetry": "x" * 4096}))
            with patch.object(sessions, "load_messages", AsyncMock(side_effect=AssertionError("full messages"))), \
                    patch.object(sessions, "load_events", AsyncMock(side_effect=AssertionError("full events"))):
                history = await load_thread_history(sessions, thread)
            self.assertTrue(history.truncated)
            self.assertEqual(history.message_count, 250)
            self.assertLessEqual(len(history.messages), 101)
            self.assertLessEqual(len(history.events), 100)
            self.assertEqual(history.task_result.execution_status, "accepted_partial")
            self.assertEqual(history.task_result, accepted_result)
            self.assertNotEqual(history.task_result.verification_status, "verified")
            state = TerminalState()
            state.restore(history)
            self.assertEqual(state.task_id, task.id)
            self.assertEqual(state.status, "accepted_partial")
            self.assertEqual(state.result.exit_code(require_verified=True), 5)


if __name__ == "__main__":
    unittest.main()
