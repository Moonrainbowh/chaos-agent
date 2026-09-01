from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.models import ActionRequest
from code_agent.sessions.edit_batch_models import EditBatchState
from code_agent.sessions.rewind_repository import RewindSessionRepository
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.snapshot_store import WorkspaceSnapshotStore
from code_agent_win.rewind_capture import RewindCaptureCoordinator
from code_agent_win.rewind_edit_batch_models import prepare_request


class _Lease:
    async def release(self) -> None:
        return None


class _Gate:
    async def acquire(self) -> _Lease:
        return _Lease()


class RewindBatchRecoveryCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_recovery_settles_rolled_back_before_propagating(self) -> None:
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
            prepared = editor.preflight_batch(plan)
            handle = snapshots.save(editor.snapshot_from_prepared(prepared))
            coverage = await sessions.ensure_rewind_coverage(
                snapshots.workspace_fingerprint
            )
            context = ActionExecutionContext(owner, owner, "recover-cancel")
            request = ActionRequest(
                "recover-cancel", "apply_workspace_edit_plan_v1", {}
            )
            record = await sessions.prepare_edit_batch(
                prepare_request(
                    capture,
                    context,
                    request,
                    prepared,
                    handle,
                    "stored-recover-cancel",
                    coverage.token,
                )
            )
            await sessions.transition_edit_batch(
                record.mutation.mutation_id, EditBatchState.APPLYING
            )
            editor.apply_batch(plan)
            started = threading.Event()
            finish = threading.Event()
            real_recover = editor.recover_batch

            def blocked_recover(*args):
                started.set()
                finish.wait(2)
                return real_recover(*args)

            with patch.object(editor, "recover_batch", side_effect=blocked_recover):
                task = asyncio.create_task(capture.recover_edit_batches())
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                task.cancel()
                finish.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            self.assertEqual(target.read_bytes(), b"before")
            settled = await sessions.get_edit_batch(record.mutation.mutation_id)
            self.assertEqual(settled.state, EditBatchState.ROLLED_BACK)


if __name__ == "__main__":
    unittest.main()
