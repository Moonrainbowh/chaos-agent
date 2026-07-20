from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import Message  # noqa: E402
from code_agent.sessions.errors import SessionCorruptionError  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402


def _event(turn: int) -> AgentEvent:
    return AgentEvent(
        EventKind.TURN_STARTED,
        {"turn": turn},
        datetime(2026, 7, 16, turn, tzinfo=timezone.utc),
    )


class CheckpointBoundTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = SQLiteSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_empty_checkpoint_records_zero_bounds(self) -> None:
        occupied = await self.repository.create_thread()
        await self.repository.append_message(occupied, Message("user", "other"))
        await self.repository.append_event(occupied, _event(1))
        thread_id = await self.repository.create_thread()

        await self.repository.create_checkpoint(thread_id, "empty")

        checkpoint = (await self.repository.list_checkpoints(thread_id))[0]
        self.assertEqual(
            (checkpoint.message_sequence, checkpoint.event_sequence), (0, 0)
        )

    async def test_bounds_use_thread_maxima_and_remain_immutable(self) -> None:
        first = await self.repository.create_thread()
        second = await self.repository.create_thread()
        await self.repository.append_message(first, Message("user", "first"))
        await self.repository.append_event(first, _event(1))
        await self.repository.append_message(second, Message("user", "other"))
        await self.repository.append_event(second, _event(2))
        await self.repository.append_message(first, Message("assistant", "third"))
        await self.repository.append_event(first, _event(3))
        await self.repository.append_message(second, Message("assistant", "fourth"))
        await self.repository.append_event(second, _event(4))

        await self.repository.create_checkpoint(first, "bounded")
        before = (await self.repository.list_checkpoints(first))[0]
        await self.repository.append_message(first, Message("user", "later"))
        await self.repository.append_event(first, _event(5))
        after = (await self.repository.list_checkpoints(first))[0]

        self.assertEqual((before.message_sequence, before.event_sequence), (3, 3))
        self.assertEqual(after, before)

    async def test_metadata_cannot_forge_checkpoint_bounds(self) -> None:
        thread_id = await self.repository.create_thread()
        await self.repository.append_message(thread_id, Message("user", "one"))

        await self.repository.create_checkpoint(
            thread_id,
            "trusted",
            {"message_sequence": 999, "event_sequence": 999},
        )

        checkpoint = (await self.repository.list_checkpoints(thread_id))[0]
        self.assertEqual(
            (checkpoint.message_sequence, checkpoint.event_sequence), (1, 0)
        )
        self.assertEqual(dict(checkpoint.metadata)["message_sequence"], 999)

    async def test_invalid_persisted_bound_fails_closed(self) -> None:
        thread_id = await self.repository.create_thread()
        await self.repository.create_checkpoint(thread_id, "damaged")
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "UPDATE checkpoints SET message_sequence = -1 WHERE thread_id = ?",
                (thread_id,),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(SessionCorruptionError):
            await self.repository.list_checkpoints(thread_id)


if __name__ == "__main__":
    unittest.main()
