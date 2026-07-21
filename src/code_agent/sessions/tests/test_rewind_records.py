from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus  # noqa: E402
from code_agent.sessions.errors import SessionNotFound  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.sessions.workspace_models import (  # noqa: E402
    CheckpointCursor,
    RewindMode,
    RewindOperationRecord,
    RewindOperationStatus,
    WorkspaceLineageRecord,
    WorkspaceLineageStatus,
    WorkspaceSnapshotStatus,
)


class RewindRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = SQLiteSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def prepare(self) -> tuple[WorkspaceLineageRecord, str, str, str]:
        thread_id = await self.repository.create_thread()
        task = await self.repository.create_task(
            thread_id,
            TaskContract("repair", TaskAuthorization.local_workspace("C:/managed")),
        )
        lineage = WorkspaceLineageRecord.create(
            repository_id="repo",
            source_root="C:/source",
            worktree_root=f"C:/managed/{uuid.uuid4().hex}",
            branch_name="codex/task-one",
            head_commit="a" * 40,
            owner_task_id=task.id,
        )
        await self.repository.create_lineage(lineage)
        empty = CheckpointCursor(
            snapshot_status=WorkspaceSnapshotStatus.UNAVAILABLE,
            lineage_id=lineage.id,
        )
        source = await self.repository.publish_workspace_checkpoint(
            thread_id, "source", {}, None, empty
        )
        rollback = await self.repository.publish_workspace_checkpoint(
            thread_id, "rollback", {}, None, empty
        )
        return lineage, task.id, source, rollback

    async def test_rewind_state_machine_cas_and_idempotence(self) -> None:
        lineage, _, source, rollback = await self.prepare()
        operation = RewindOperationRecord.create(
            lineage.id,
            source,
            rollback,
            RewindMode.CODE,
            "b" * 64,
        )

        created = await self.repository.begin_rewind(operation)
        repeated = await self.repository.begin_rewind(operation)
        completed = await self.repository.complete_rewind(operation.id)
        completed_again = await self.repository.complete_rewind(operation.id)

        self.assertEqual(created, repeated)
        self.assertEqual(completed, completed_again)
        self.assertEqual(completed.status, RewindOperationStatus.COMPLETED)
        with self.assertRaises(ValueError):
            await self.repository.fail_rewind(operation.id, "restore_failed")

    async def test_session_completion_requires_replacement_from_same_lineage(self) -> None:
        lineage, _, source, rollback = await self.prepare()
        operation = RewindOperationRecord.create(
            lineage.id,
            source,
            rollback,
            RewindMode.SESSION,
            "b" * 64,
        )
        await self.repository.begin_rewind(operation)

        with self.assertRaises(ValueError):
            await self.repository.complete_rewind(operation.id)
        other_thread = await self.repository.create_thread()
        other_task = await self.repository.create_task(
            other_thread,
            TaskContract("other", TaskAuthorization.local_workspace("C:/other")),
        )
        with self.assertRaises(ValueError):
            await self.repository.complete_rewind(operation.id, other_task.id)

    async def test_fail_rewind_rolls_back_or_blocks_lineage(self) -> None:
        lineage, _, source, rollback = await self.prepare()
        rolled = RewindOperationRecord.create(
            lineage.id, source, rollback, RewindMode.CODE, "b" * 64
        )
        await self.repository.begin_rewind(rolled)
        result = await self.repository.fail_rewind(rolled.id, "restore_failed")
        self.assertEqual(result.status, RewindOperationStatus.ROLLED_BACK)

        blocked = RewindOperationRecord.create(
            lineage.id, source, rollback, RewindMode.CODE, "c" * 64
        )
        await self.repository.begin_rewind(blocked)
        result = await self.repository.fail_rewind(
            blocked.id, "rollback_failed", recovery_required=True
        )
        self.assertEqual(result.status, RewindOperationStatus.RECOVERY_REQUIRED)
        self.assertEqual(
            (await self.repository.load_lineage(lineage.id)).status,
            WorkspaceLineageStatus.RECOVERY_REQUIRED,
        )
        with self.assertRaises(ValueError):
            await self.repository.begin_rewind(
                RewindOperationRecord.create(
                    lineage.id, source, rollback, RewindMode.CODE, "d" * 64
                )
            )

    async def test_pending_queries_are_ordered_and_lineage_isolated(self) -> None:
        first, _, source_one, rollback_one = await self.prepare()
        second, _, source_two, rollback_two = await self.prepare()
        first_op = RewindOperationRecord.create(
            first.id, source_one, rollback_one, RewindMode.CODE, "a" * 64
        )
        second_op = RewindOperationRecord.create(
            second.id, source_two, rollback_two, RewindMode.CODE, "b" * 64
        )
        await asyncio.gather(
            self.repository.begin_rewind(first_op),
            self.repository.begin_rewind(second_op),
        )

        all_pending = await self.repository.pending_rewinds()
        isolated = await self.repository.pending_rewinds(first.id)

        self.assertEqual({item.id for item in all_pending}, {first_op.id, second_op.id})
        self.assertEqual(isolated, (first_op,))
        self.assertEqual(
            list(all_pending), sorted(all_pending, key=lambda item: (item.created_at, item.id))
        )

    async def test_cross_lineage_checkpoint_is_rejected_without_partial_operation(self) -> None:
        first, _, source_one, _ = await self.prepare()
        _, _, _, rollback_two = await self.prepare()
        operation = RewindOperationRecord.create(
            first.id, source_one, rollback_two, RewindMode.CODE, "b" * 64
        )

        with self.assertRaises(ValueError):
            await self.repository.begin_rewind(operation)
        self.assertEqual(await self.repository.pending_rewinds(first.id), ())

    async def test_missing_operation_fails_closed(self) -> None:
        with self.assertRaises(SessionNotFound):
            await self.repository.complete_rewind(uuid.uuid4().hex)


if __name__ == "__main__":
    unittest.main()
