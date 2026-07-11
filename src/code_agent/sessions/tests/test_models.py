from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.sessions.models import (  # noqa: E402
    CheckpointRecord,
    GoalRecord,
    GoalStatus,
    ThreadStatus,
    ThreadSummary,
)


class SessionModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 11, tzinfo=timezone.utc)

    def test_status_values_are_stable(self) -> None:
        self.assertEqual([item.value for item in ThreadStatus], ["active", "archived"])
        self.assertEqual(
            [item.value for item in GoalStatus],
            ["active", "completed", "blocked"],
        )

    def test_thread_summary_is_frozen_and_validated(self) -> None:
        summary = ThreadSummary(
            id="thread-1",
            title="Work item",
            status=ThreadStatus.ACTIVE,
            created_at=self.now,
            updated_at=self.now,
            message_count=2,
            last_message_preview="latest",
        )

        self.assertEqual(summary.message_count, 2)
        with self.assertRaises(FrozenInstanceError):
            summary.title = "changed"  # type: ignore[misc]
        with self.assertRaises(ValueError):
            ThreadSummary(
                id="",
                title=None,
                status=ThreadStatus.ACTIVE,
                created_at=self.now,
                updated_at=self.now,
                message_count=0,
                last_message_preview=None,
            )

    def test_goal_and_checkpoint_copy_metadata(self) -> None:
        goal_metadata = {"priority": "high"}
        checkpoint_metadata = {"revision": 3}
        goal = GoalRecord(
            id="goal-1",
            thread_id="thread-1",
            objective="Ship feature",
            status=GoalStatus.ACTIVE,
            metadata=goal_metadata,
            created_at=self.now,
            updated_at=self.now,
        )
        checkpoint = CheckpointRecord(
            id="checkpoint-1",
            thread_id="thread-1",
            label="before edit",
            metadata=checkpoint_metadata,
            created_at=self.now,
        )

        goal_metadata["priority"] = "low"
        checkpoint_metadata["revision"] = 4

        self.assertEqual(dict(goal.metadata), {"priority": "high"})
        self.assertEqual(dict(checkpoint.metadata), {"revision": 3})
        with self.assertRaises(TypeError):
            goal.metadata["priority"] = "low"  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
