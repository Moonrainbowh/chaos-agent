from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import Message  # noqa: E402
from code_agent.interfaces.history import load_thread_history  # noqa: E402
from code_agent.sessions.models import (  # noqa: E402
    CheckpointRecord,
    GoalRecord,
    GoalStatus,
)


class InMemoryHistoryReader:
    def __init__(
        self,
        messages: Sequence[Message],
        events: Sequence[AgentEvent],
        goals: Sequence[GoalRecord],
        checkpoints: Sequence[CheckpointRecord],
    ) -> None:
        self.messages = messages
        self.events = events
        self.goals = goals
        self.checkpoints = checkpoints

    async def load_messages(self, thread_id: str) -> Sequence[Message]:
        return self.messages

    async def load_events(self, thread_id: str) -> Sequence[AgentEvent]:
        return self.events

    async def list_goals(self, thread_id: str) -> Sequence[GoalRecord]:
        return self.goals

    async def list_checkpoints(
        self, thread_id: str
    ) -> Sequence[CheckpointRecord]:
        return self.checkpoints


class LoadThreadHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_each_record_collection_for_the_requested_thread(self) -> None:
        timestamp = datetime(2026, 7, 11, tzinfo=timezone.utc)
        messages = (Message("user", "resume this task"),)
        events = (AgentEvent(EventKind.RUN_STARTED, {"thread_id": "thread-1"}, timestamp),)
        goals = (
            GoalRecord(
                id="goal-1",
                thread_id="thread-1",
                objective="Restore the session",
                status=GoalStatus.ACTIVE,
                created_at=timestamp,
                updated_at=timestamp,
            ),
        )
        checkpoints = (
            CheckpointRecord(
                id="checkpoint-1",
                thread_id="thread-1",
                label="before resume",
                created_at=timestamp,
            ),
        )
        reader = InMemoryHistoryReader(messages, events, goals, checkpoints)

        history = await load_thread_history(reader, "thread-1")

        self.assertEqual(history.thread_id, "thread-1")
        self.assertEqual(history.messages, messages)
        self.assertEqual(history.events, events)
        self.assertEqual(history.goals, goals)
        self.assertEqual(history.checkpoints, checkpoints)

    async def test_blank_thread_id_raises_value_error(self) -> None:
        reader = InMemoryHistoryReader((), (), (), ())

        with self.assertRaisesRegex(ValueError, "thread_id must be non-blank text"):
            await load_thread_history(reader, "")


if __name__ == "__main__":
    unittest.main()
