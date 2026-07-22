from __future__ import annotations

import sys
import tempfile
import unittest
import uuid
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.limits import EngineLimits  # noqa: E402
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.sessions.workspace_models import (  # noqa: E402
    CheckpointCursor,
    WorkspaceLineageRecord,
    WorkspaceSnapshotStatus,
)


class LineageBudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = SQLiteSessionRepository(
            Path(self.temporary.name) / "sessions.sqlite3"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def prepare(self) -> tuple[str, str, str]:
        thread_id = await self.repository.create_thread()
        task = await self.repository.create_task(
            thread_id,
            TaskContract("repair", TaskAuthorization.local_workspace("C:/managed")),
        )
        await self.repository.get_or_create_task_budget(
            thread_id, "model", EngineLimits(max_agent_rounds=20, max_tool_calls=20)
        )
        lineage = WorkspaceLineageRecord.create(
            repository_id="repo",
            source_root="C:/source",
            worktree_root=f"C:/managed/{uuid.uuid4().hex}",
            branch_name="codex/task",
            head_commit="a" * 40,
            owner_task_id=task.id,
        )
        await self.repository.create_lineage(lineage)
        checkpoint = await self.repository.publish_workspace_checkpoint(
            thread_id,
            "source",
            {},
            None,
            CheckpointCursor(
                lineage_id=lineage.id,
                snapshot_status=WorkspaceSnapshotStatus.UNAVAILABLE,
            ),
        )
        return task.id, lineage.id, checkpoint

    async def pause(self, task_id: str) -> None:
        task = await self.repository.load_task(task_id)
        if task.status is TaskStatus.CREATED:
            await self.repository.transition_task(task_id, TaskStatus.RUNNING)
        await self.repository.transition_task(task_id, TaskStatus.PAUSED)

    async def test_signature_and_matching_count_follow_current_owner_across_forks(self) -> None:
        original, lineage_id, checkpoint = await self.prepare()
        await self.repository.observe_task_validation(original, "failure-a", 1)
        await self.repository.observe_task_validation(original, "failure-a", 1)

        first = await self.repository.fork_task_from_checkpoint(checkpoint)
        first_budget = await self.repository.load_task_budget(first.id)
        self.assertEqual(
            (first_budget.repeated_failures, first_budget.last_failure_signature),
            (2, "failure-a"),
        )
        await self.pause(original)
        await self.repository.transfer_lineage_owner(lineage_id, original, first.id)
        await self.repository.observe_task_validation(first.id, "failure-b", 1)

        second = await self.repository.fork_task_from_checkpoint(checkpoint)
        second_budget = await self.repository.load_task_budget(second.id)
        self.assertEqual(
            (second_budget.repeated_failures, second_budget.last_failure_signature),
            (1, "failure-b"),
        )
        await self.pause(first.id)
        await self.repository.transfer_lineage_owner(lineage_id, first.id, second.id)

        repeated = await self.repository.observe_task_validation(
            second.id, "failure-b", 1
        )
        self.assertEqual(
            (repeated.repeated_failures, repeated.last_failure_signature),
            (2, "failure-b"),
        )


if __name__ == "__main__":
    unittest.main()
