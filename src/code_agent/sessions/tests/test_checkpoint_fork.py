from __future__ import annotations

import sys
import tempfile
import unittest
import uuid
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.limits import EngineLimits  # noqa: E402
from code_agent.core.models import Message, Usage  # noqa: E402
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus  # noqa: E402
from code_agent.core.task_state import TaskState  # noqa: E402
from code_agent.sessions.models import GoalStatus  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.sessions.workspace_models import (  # noqa: E402
    CheckpointCursor,
    WorkspaceLineageRecord,
    WorkspaceSnapshotStatus,
)


class CheckpointForkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = SQLiteSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def prepare_checkpoint(self) -> tuple[str, str, str]:
        thread_id = await self.repository.create_thread("original")
        task = await self.repository.create_task(
            thread_id,
            TaskContract("repair", TaskAuthorization.local_workspace("C:/managed")),
        )
        await self.repository.get_or_create_task_budget(
            thread_id, "model", EngineLimits(max_agent_rounds=20, max_tool_calls=20)
        )
        await self.repository.reserve_task_budget(thread_id, model_turns=2, tool_calls=3)
        await self.repository.consume_task_usage(task.id, Usage(10, 5))
        goal_id = await self.repository.create_goal(
            thread_id, "ship", metadata={"priority": 1}
        )
        await self.repository.update_goal(goal_id, GoalStatus.COMPLETED)
        state = TaskState(objective="repair", verified_facts=("before",))
        await self.repository.save_task_state(thread_id, state)
        await self.repository.append_message(thread_id, Message("user", "before"))
        await self.repository.append_event(
            thread_id, AgentEvent(EventKind.TURN_STARTED, {"turn": 1})
        )
        message_sequence = (await self.repository.load_message_records(thread_id))[-1].sequence
        event_sequence = await self.repository.latest_event_sequence(thread_id)
        budget = await self.repository.load_task_budget(task.id)
        lineage = WorkspaceLineageRecord.create(
            repository_id="repo",
            source_root="C:/source",
            worktree_root=f"C:/managed/{uuid.uuid4().hex}",
            branch_name="codex/task-one",
            head_commit="a" * 40,
            owner_task_id=task.id,
        )
        await self.repository.create_lineage(lineage)
        cursor = CheckpointCursor.from_records(
            message_sequence,
            event_sequence,
            await self.repository.list_goals(thread_id),
            state,
            budget,
            WorkspaceSnapshotStatus.UNAVAILABLE,
            lineage.id,
        )
        checkpoint = await self.repository.publish_workspace_checkpoint(
            thread_id, "paused", {"task_id": task.id}, None, cursor
        )
        return task.id, lineage.id, checkpoint

    async def test_fork_is_non_destructive_and_excludes_later_records(self) -> None:
        old_task_id, lineage_id, checkpoint = await self.prepare_checkpoint()
        old = await self.repository.load_task(old_task_id)
        await self.repository.append_message(old.thread_id, Message("user", "after"))
        await self.repository.append_event(
            old.thread_id, AgentEvent(EventKind.ACTION_COMPLETED, {"turn": 1})
        )
        await self.repository.save_task_state(
            old.thread_id, TaskState(objective="repair", verified_facts=("after",))
        )
        await self.repository.consume_task_usage(old.id, Usage(4, 3))

        forked = await self.repository.fork_task_from_checkpoint(checkpoint)

        self.assertEqual([item.content for item in await self.repository.load_messages(forked.thread_id)], ["before"])
        self.assertEqual(len(await self.repository.load_events(forked.thread_id)), 1)
        self.assertEqual((await self.repository.load_task_state(forked.thread_id)).verified_facts, ("before",))
        self.assertEqual((await self.repository.list_goals(forked.thread_id))[0].status, GoalStatus.COMPLETED)
        fork_budget = await self.repository.load_task_budget(forked.id)
        self.assertEqual((fork_budget.model_turns, fork_budget.tool_calls), (2, 3))
        self.assertEqual((fork_budget.input_tokens, fork_budget.output_tokens), (14, 8))
        self.assertEqual(await self.repository.load_task(old.id), old)
        self.assertEqual((await self.repository.load_lineage_for_task(forked.id)).id, lineage_id)

    async def test_owner_transfer_is_atomic_cas_idempotent_and_requires_legal_tasks(self) -> None:
        old_task_id, lineage_id, checkpoint = await self.prepare_checkpoint()
        replacement = await self.repository.fork_task_from_checkpoint(checkpoint)
        await self.repository.transition_task(old_task_id, TaskStatus.RUNNING)
        with self.assertRaises(ValueError):
            await self.repository.transfer_lineage_owner(
                lineage_id, old_task_id, replacement.id
            )
        await self.repository.transition_task(old_task_id, TaskStatus.PAUSED)

        transferred = await self.repository.transfer_lineage_owner(
            lineage_id, old_task_id, replacement.id
        )
        repeated = await self.repository.transfer_lineage_owner(
            lineage_id, old_task_id, replacement.id
        )

        self.assertEqual(transferred.owner_task_id, replacement.id)
        self.assertEqual(repeated, transferred)
        with self.assertRaises(ValueError):
            await self.repository.transfer_lineage_owner(
                lineage_id, uuid.uuid4().hex, old_task_id
            )

    async def test_checkpoint_sequences_must_match_thread_boundary(self) -> None:
        thread_id = await self.repository.create_thread()
        await self.repository.append_message(thread_id, Message("user", "one"))
        with self.assertRaises(ValueError):
            await self.repository.publish_workspace_checkpoint(
                thread_id,
                "bad cursor",
                {},
                None,
                CheckpointCursor(snapshot_status=WorkspaceSnapshotStatus.UNAVAILABLE),
            )

    async def test_repeated_fork_cannot_reset_usage_consumed_by_replacement(self) -> None:
        old_task_id, lineage_id, checkpoint = await self.prepare_checkpoint()
        first = await self.repository.fork_task_from_checkpoint(checkpoint)
        await self.repository.transition_task(old_task_id, TaskStatus.RUNNING)
        await self.repository.transition_task(old_task_id, TaskStatus.PAUSED)
        await self.repository.transfer_lineage_owner(lineage_id, old_task_id, first.id)
        await self.repository.consume_task_usage(first.id, Usage(7, 6))

        second = await self.repository.fork_task_from_checkpoint(checkpoint)
        budget = await self.repository.load_task_budget(second.id)

        self.assertEqual((budget.input_tokens, budget.output_tokens), (17, 11))


if __name__ == "__main__":
    unittest.main()
