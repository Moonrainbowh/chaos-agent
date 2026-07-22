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
    RewindMode,
    RewindOperationRecord,
    RewindOperationStatus,
    WorkspaceLineageRecord,
    WorkspaceSnapshotStatus,
)


class RewindCompletionInvariantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = SQLiteSessionRepository(
            Path(self.temporary.name) / "sessions.sqlite3"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def prepare(
        self, mode: RewindMode = RewindMode.SESSION
    ) -> tuple[str, str, str, str, RewindOperationRecord]:
        thread_id = await self.repository.create_thread()
        source_task = await self.repository.create_task(
            thread_id,
            TaskContract("source", TaskAuthorization.local_workspace("C:/managed")),
        )
        await self.repository.get_or_create_task_budget(
            thread_id, "model", EngineLimits()
        )
        lineage = WorkspaceLineageRecord.create(
            repository_id="repo",
            source_root="C:/source",
            worktree_root=f"C:/managed/{uuid.uuid4().hex}",
            branch_name="codex/source",
            head_commit="a" * 40,
            owner_task_id=source_task.id,
        )
        await self.repository.create_lineage(lineage)
        cursor = CheckpointCursor(
            lineage_id=lineage.id,
            snapshot_status=WorkspaceSnapshotStatus.UNAVAILABLE,
        )
        source = await self.repository.publish_workspace_checkpoint(
            thread_id, "source", {}, None, cursor
        )
        rollback = await self.repository.publish_workspace_checkpoint(
            thread_id, "rollback", {}, None, cursor
        )
        operation = RewindOperationRecord.create(
            lineage.id,
            source,
            rollback,
            mode,
            "b" * 64,
        )
        await self.repository.begin_rewind(operation)
        replacement = await self.repository.fork_task_from_checkpoint(source)
        return source_task.id, replacement.id, lineage.id, source, operation

    async def assert_pending(self, lineage_id: str, operation_id: str) -> None:
        pending = await self.repository.pending_rewinds(lineage_id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].id, operation_id)
        self.assertEqual(pending[0].status, RewindOperationStatus.PENDING)

    async def test_generic_completion_rejects_legacy_session_bypass(self) -> None:
        for mode in (RewindMode.SESSION, RewindMode.CODE_AND_SESSION):
            with self.subTest(mode=mode):
                source, replacement, lineage, _, operation = await self.prepare(mode)
                await self.repository.transition_task(source, TaskStatus.RUNNING)
                await self.repository.transition_task(source, TaskStatus.PAUSED)
                await self.repository.transfer_lineage_owner(
                    lineage, source, replacement
                )
                await self.repository.transition_task(replacement, TaskStatus.RUNNING)
                await self.repository.transition_task(replacement, TaskStatus.PAUSED)

                with self.assertRaisesRegex(ValueError, "atomic"):
                    await self.repository.complete_rewind(operation.id, replacement)

                await self.assert_pending(lineage, operation.id)


if __name__ == "__main__":
    unittest.main()
