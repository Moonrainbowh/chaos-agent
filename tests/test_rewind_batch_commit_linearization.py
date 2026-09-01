from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.models import ActionRequest
from code_agent.sessions.edit_batch_models import EditBatchState
from code_agent.sessions.rewind_repository import RewindSessionRepository
from code_agent.workspace.edits import BatchApplyStatus, WorkspaceEditor
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.snapshot_store import WorkspaceSnapshotStore
from code_agent_win.rewind_capture import RewindCaptureCoordinator


class _Lease:
    async def release(self) -> None:
        return None


class _Gate:
    async def acquire(self) -> _Lease:
        return _Lease()


class RewindBatchCommitLinearizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_commit_wins_over_concurrent_task_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "workspace"
            root.mkdir()
            target = root / "changed.txt"
            target.write_bytes(b"before")
            editor = WorkspaceEditor(WorkspacePathGuard(root))
            snapshots = WorkspaceSnapshotStore(editor.guard, base / "snapshots")
            sessions = RewindSessionRepository(base / "sessions.sqlite3")
            owner = await sessions.create_thread()
            capture = RewindCaptureCoordinator(sessions, editor, snapshots, _Gate())
            plan = editor.plan_batch((editor.plan_write("changed.txt", "after"),))
            context = ActionExecutionContext(owner, owner, "commit-race")
            request = ActionRequest(
                "commit-race", "apply_workspace_edit_plan_v1", {}
            )
            reached_commit = asyncio.Event()
            release_commit = asyncio.Event()
            settle = sessions.settle_edit_batch

            async def blocked_settle(mutation_id, state, **kwargs):
                if state is EditBatchState.COMPLETED:
                    reached_commit.set()
                    await release_commit.wait()
                return await settle(mutation_id, state, **kwargs)

            with patch.object(sessions, "settle_edit_batch", blocked_settle):
                task = asyncio.create_task(
                    capture.apply_edit_plan(context, request, plan, "stored-plan")
                )
                await asyncio.wait_for(reached_commit.wait(), 2)
                task.cancel()
                release_commit.set()
                result = await task

            self.assertEqual(result.status, BatchApplyStatus.APPLIED)
            self.assertEqual(target.read_bytes(), b"after")
            record = (await sessions.list_edit_batches(
                snapshots.workspace_fingerprint
            ))[0]
            self.assertEqual(record.state, EditBatchState.COMPLETED)


if __name__ == "__main__":
    unittest.main()
