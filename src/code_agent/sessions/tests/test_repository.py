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
from code_agent.core.models import Message, ToolCall  # noqa: E402
from code_agent.core.protocols import SessionRepository  # noqa: E402
from code_agent.sessions.errors import SessionNotFound  # noqa: E402
from code_agent.sessions.models import GoalStatus, ThreadStatus  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
