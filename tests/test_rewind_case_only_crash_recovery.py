from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.models import ActionRequest
from code_agent.sessions.edit_batch_models import EditBatchState
from code_agent.sessions.rewind_repository import RewindSessionRepository
from code_agent.workspace.edits import BatchApplyStatus, WorkspaceEditor
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


@unittest.skipUnless(os.name == "nt", "Windows exact-name recovery")
class CaseOnlyCrashRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_restores_original_exact_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "workspace"
            root.mkdir()
            (root / "MixedCase.txt").write_bytes(b"before")
            editor = WorkspaceEditor(WorkspacePathGuard(root))
            snapshots = WorkspaceSnapshotStore(
                editor.guard, base / "state" / "rewind-snapshots"
            )
            sessions = RewindSessionRepository(base / "sessions.sqlite3")
            owner = await sessions.create_thread()
            capture = RewindCaptureCoordinator(
                sessions, editor, snapshots, _Gate()
            )
            plan = editor.plan_batch(
                (editor.plan_move("MixedCase.txt", "MIXEDCASE.txt"),)
            )
            prepared = editor.preflight_batch(plan)
            handle = snapshots.save(editor.snapshot_from_prepared(prepared))
            coverage = await sessions.ensure_rewind_coverage(
                snapshots.workspace_fingerprint
            )
            context = ActionExecutionContext(owner, owner, "case-crash")
            request = ActionRequest(
                "case-crash", "apply_workspace_edit_plan_v1", {}
            )
            journal = prepare_request(
                capture,
                context,
                request,
                prepared,
                handle,
                "stored-case-crash",
                coverage.token,
            )
            record = await sessions.prepare_edit_batch(journal)
            await sessions.transition_edit_batch(
                record.mutation.mutation_id, EditBatchState.APPLYING
            )
            self.assertEqual(
                editor.apply_batch(plan).status, BatchApplyStatus.APPLIED
            )
            self.assertEqual(os.listdir(root), ["MIXEDCASE.txt"])

            reopened = RewindSessionRepository(base / "sessions.sqlite3")
            recovered_editor = WorkspaceEditor(WorkspacePathGuard(root))
            recovered_snapshots = WorkspaceSnapshotStore(
                recovered_editor.guard, base / "state" / "rewind-snapshots"
            )
            recovered = RewindCaptureCoordinator(
                reopened, recovered_editor, recovered_snapshots, _Gate()
            )
            results = await recovered.recover_edit_batches()

            self.assertEqual(results[0].status, BatchApplyStatus.ROLLED_BACK)
            self.assertEqual(os.listdir(root), ["MixedCase.txt"])
            settled = await reopened.get_edit_batch(record.mutation.mutation_id)
            self.assertEqual(settled.state, EditBatchState.ROLLED_BACK)


if __name__ == "__main__":
    unittest.main()
