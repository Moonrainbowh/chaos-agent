from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import ActionRequest, ActionResult, Message, ToolCall  # noqa: E402
from code_agent.core.task_state import TaskState  # noqa: E402
from code_agent.core.protocols import SessionRepository  # noqa: E402
from code_agent.sessions.errors import SessionNotFound  # noqa: E402
from code_agent.sessions.models import GoalStatus, ThreadStatus  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.core.limits import EngineLimits  # noqa: E402
from code_agent.core.models import Usage  # noqa: E402
from code_agent.core.task import TaskAuthorization, TaskContract  # noqa: E402


class SQLiteSessionRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = SQLiteSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_core_protocol_round_trips_messages_events_after_restart(self) -> None:
        repository: SessionRepository = self.repository
        thread_id = await repository.create_thread()
        message = Message(
            role="assistant",
            content="working",
            tool_calls=(
                ToolCall(id="call-1", name="read_file", arguments={"path": "a.py"}),
            ),
        )
        event = AgentEvent(
            EventKind.ACTION_COMPLETED,
            {"ok": True, "usage": {"input_tokens": 4}},
            datetime(2026, 7, 11, 1, 2, 3, tzinfo=timezone.utc),
        )

        await repository.append_message(thread_id, message)
        await repository.append_event(thread_id, event)

        reopened = SQLiteSessionRepository(self.database)
        self.assertEqual(tuple(await reopened.load_messages(thread_id)), (message,))
        self.assertEqual(await reopened.load_events(thread_id), (event,))

    async def test_tui_thread_list_orders_activity_and_exposes_preview(self) -> None:
        first = await self.repository.create_thread(title="First task")
        await asyncio.sleep(0.01)
        second = await self.repository.create_thread(title="Second task")
        await asyncio.sleep(0.01)
        await self.repository.append_message(first, Message("user", "latest\nrequest"))

        summaries = await self.repository.list_threads()

        self.assertEqual([item.id for item in summaries], [first, second])
        self.assertEqual(summaries[0].title, "First task")
        self.assertEqual(summaries[0].message_count, 1)
        self.assertEqual(summaries[0].last_message_preview, "latest request")
        self.assertEqual(summaries[0].status, ThreadStatus.ACTIVE)

    async def test_archived_threads_are_hidden_by_default(self) -> None:
        thread_id = await self.repository.create_thread(title="Done")

        await self.repository.archive_thread(thread_id)

        self.assertEqual(await self.repository.list_threads(), ())
        archived = await self.repository.list_threads(include_archived=True)
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].status, ThreadStatus.ARCHIVED)

    async def test_goals_and_checkpoints_persist_structured_metadata(self) -> None:
        thread_id = await self.repository.create_thread()
        goal_id = await self.repository.create_goal(
            thread_id, "Finish sessions", metadata={"priority": 1}
        )
        checkpoint_id = await self.repository.create_checkpoint(
            thread_id, "before migration", {"commit": "abc"}
        )

        await self.repository.update_goal(goal_id, GoalStatus.COMPLETED)

        reopened = SQLiteSessionRepository(self.database)
        goals = await reopened.list_goals(thread_id)
        checkpoints = await reopened.list_checkpoints(thread_id)
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0].id, goal_id)
        self.assertEqual(goals[0].status, GoalStatus.COMPLETED)
        self.assertEqual(dict(goals[0].metadata), {"priority": 1})
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0].id, checkpoint_id)
        self.assertEqual(dict(checkpoints[0].metadata), {"commit": "abc"})

    async def test_task_state_survives_reopen_and_reduces_atomically(self) -> None:
        repository: SessionRepository = self.repository
        thread_id = await repository.create_thread()
        state = TaskState(objective="repair startup", open_questions=("where?",))

        await repository.save_task_state(thread_id, state)
        self.assertEqual(await repository.load_task_state(thread_id), state)
        reduced = await repository.reduce_task_state(
            thread_id,
            ActionRequest("read-1", "read_file", {"path": "src/app.py"}),
            ActionResult("read-1", "read_file", {"text": "contents"}),
        )
        reopened = SQLiteSessionRepository(self.database)

        self.assertEqual(reduced.files_read, ("src/app.py",))
        self.assertEqual(await reopened.load_task_state(thread_id), reduced)

    async def test_unknown_thread_fails_closed_for_all_owned_records(self) -> None:
        operations = (
            self.repository.load_messages("missing"),
            self.repository.load_events("missing"),
            self.repository.append_message("missing", Message("user", "hello")),
            self.repository.append_event(
                "missing", AgentEvent(EventKind.ERROR, {"message": "failed"})
            ),
            self.repository.create_goal("missing", "goal"),
            self.repository.create_checkpoint("missing", "checkpoint"),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(SessionNotFound):
                    await operation

    async def test_task_budget_survives_restart_and_refuses_over_budget_reservation(self) -> None:
        thread_id = await self.repository.create_thread()
        limits = EngineLimits(max_agent_rounds=2, max_tool_calls=3)
        created = await self.repository.get_or_create_task_budget(thread_id, "model-a", limits)
        reserved = await self.repository.reserve_task_budget(thread_id, model_turns=1, tool_calls=2)
        reopened = SQLiteSessionRepository(self.database)

        self.assertEqual(created.model_turns, 0)
        self.assertEqual(reserved.model_turns, 1)
        self.assertEqual(reserved.tool_calls, 2)
        self.assertIsNone(await reopened.reserve_task_budget(thread_id, tool_calls=2))

    async def test_task_controls_and_supervision_budget_survive_restart(self) -> None:
        thread_id = await self.repository.create_thread()
        task = await self.repository.create_task(
            thread_id,
            TaskContract("repair", TaskAuthorization.local_workspace(self.temporary.name)),
        )
        await self.repository.get_or_create_task_budget(thread_id, "model-a", EngineLimits())
        await self.repository.consume_task_usage(task.id, Usage(4, 3))
        await self.repository.observe_task_validation(task.id, "pytest|1", 1)
        await self.repository.observe_task_validation(task.id, "pytest|1", 1)
        await self.repository.record_task_active_seconds(task.id, 42)
        await self.repository.record_task_control(task.id, "stop editing and inspect tests")

        reopened = SQLiteSessionRepository(self.database)
        budget = await reopened.load_task_budget(task.id)

        self.assertEqual((budget.input_tokens, budget.output_tokens), (4, 3))
        self.assertEqual((budget.repair_cycles, budget.repeated_failures), (2, 2))
        self.assertEqual(budget.active_seconds, 42)
        self.assertEqual(
            await reopened.consume_task_controls(task.id),
            ("stop editing and inspect tests",),
        )
        self.assertEqual(await reopened.consume_task_controls(task.id), ())


if __name__ == "__main__":
    unittest.main()
