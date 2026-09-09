"""Prove lazy recovery preserves durable conflict checks and the mutation gate."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.models import ActionRequest
from code_agent.sessions.edit_batch_models import EditBatchState
from code_agent.sessions.rewind_repository import RewindSessionRepository
from code_agent.workspace.edits import BatchApplyStatus, WorkspaceEditor
from code_agent.workspace.snapshot_store import WorkspaceSnapshotStore
from code_agent_win.rewind_capture import RewindCaptureCoordinator
from code_agent_win.rewind_edit_batch_models import prepare_request
from code_agent_win.rewind_gate import WorkspaceMutationGate
from code_agent_win.workspace_mutation_pool import WorkspaceMutationPool
from code_agent_win.workspace_startup_recovery import (
    WorkspaceBatchRecoveryConflict, recover_workspace_edit_batches,
)
from tests.test_task_scoped_mutations import _service


class StartupRecoveryFastPathTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name).resolve()
        self.source_root, self.task_root = base / "source", base / "task"
        self.source_root.mkdir()
        self.task_root.mkdir()
        self.source, self.task = _service(self.source_root), _service(self.task_root)
        self.sessions = RewindSessionRepository(base / "sessions.sqlite3")
        self.owner = await self.sessions.create_thread()
        snapshots = WorkspaceSnapshotStore(self.source.guard, base / "snapshots")
        self.capture = RewindCaptureCoordinator(
            self.sessions, WorkspaceEditor(self.source.guard), snapshots,
            WorkspaceMutationGate(base / "state", snapshots.workspace_fingerprint),
        )
        self.pool = WorkspaceMutationPool(self.source, self.capture)
        self.built = []
        self.runtime = SimpleNamespace(
            _task_roots={"task": self.task_root}, _thread_roots={"thread": self.task_root},
            services_for_root=self._build,
        )

    async def asyncTearDown(self):
        self.temporary.cleanup()

    def _build(self, root):
        self.built.append(root)
        return self.source if root == self.source_root else self.task

    async def _pending(self, bundle):
        editor, capture = bundle.editor, bundle.capture
        (self.task_root / "note.txt").write_text("before")
        plan = editor.plan_batch((editor.plan_write("note.txt", "after"),))
        prepared = editor.preflight_batch(plan)
        handle = capture.snapshots.save(editor.snapshot_from_prepared(prepared))
        coverage = await self.sessions.ensure_rewind_coverage(bundle.workspace_fingerprint)
        record = await self.sessions.prepare_edit_batch(prepare_request(
            capture, ActionExecutionContext(self.owner, self.owner, "crash"),
            ActionRequest("crash", "apply_workspace_edit_plan_v1", {}),
            prepared, handle, "stored-crash", coverage.token,
        ))
        await self.sessions.transition_edit_batch(record.mutation.mutation_id, EditBatchState.APPLYING)
        editor.apply_batch(plan)
        return record

    async def test_empty_history_builds_no_services_or_mutation_bundles(self):
        self.assertEqual(await recover_workspace_edit_batches(
            self.runtime, self.pool, self.source_root,
        ), ())
        self.assertEqual(self.built, [])
        self.assertEqual(len(self.pool._bundles), 1)

    async def test_pending_task_is_recovered_and_settled_without_building_source(self):
        record = await self._pending(self.pool.for_services(self.task))
        self.pool = WorkspaceMutationPool(self.source, self.capture)
        result = await recover_workspace_edit_batches(self.runtime, self.pool, self.source_root)
        self.assertEqual(self.built, [self.task_root])
        self.assertEqual(result[0].status, BatchApplyStatus.ROLLED_BACK)
        self.assertEqual((self.task_root / "note.txt").read_text(), "before")
        self.assertEqual((await self.sessions.get_edit_batch(record.mutation.mutation_id)).state, EditBatchState.ROLLED_BACK)
        self.built.clear()
        self.assertEqual(await recover_workspace_edit_batches(self.runtime, self.pool, self.source_root), ())
        self.assertEqual(self.built, [])

    async def test_foreign_state_blocks_both_initial_and_repeated_startup(self):
        await self._pending(self.pool.for_services(self.task))
        (self.task_root / "note.txt").write_text("user change")
        for _ in range(2):
            self.pool = WorkspaceMutationPool(self.source, self.capture)
            with self.assertRaises(WorkspaceBatchRecoveryConflict):
                await recover_workspace_edit_batches(self.runtime, self.pool, self.source_root)
            self.assertEqual((self.task_root / "note.txt").read_text(), "user change")

    async def test_probe_waits_for_writer_and_observes_its_new_record(self):
        bundle = self.pool.for_services(self.task)
        lease = await bundle.gate.acquire()
        fresh = WorkspaceMutationPool(self.source, self.capture)
        entered = asyncio.Event()
        acquire = WorkspaceMutationGate.acquire

        async def observed(gate, **kwargs):
            entered.set()
            return await acquire(gate, **kwargs)

        try:
            with patch.object(WorkspaceMutationGate, "acquire", observed):
                probe = asyncio.create_task(fresh.needs_edit_batch_recovery(self.task_root))
                await asyncio.wait_for(entered.wait(), 2)
                self.assertFalse(probe.done())
                await self._pending(bundle)
                await lease.release()
                self.assertTrue(await asyncio.wait_for(probe, 2))
        finally:
            await lease.release()

    async def test_query_failure_is_not_treated_as_empty_and_releases_gate(self):
        with patch.object(self.sessions, "list_unresolved_edit_batches", new=AsyncMock(side_effect=RuntimeError("unreadable"))):
            with self.assertRaisesRegex(RuntimeError, "unreadable"):
                await recover_workspace_edit_batches(self.runtime, self.pool, self.source_root)
        self.assertEqual(self.built, [])
        lease = await self.capture.gate.acquire(timeout_s=.2)
        await lease.release()

    async def test_cancellation_releases_probe_gate(self):
        entered = asyncio.Event()

        async def blocked(_):
            entered.set()
            await asyncio.Future()

        with patch.object(self.sessions, "list_unresolved_edit_batches", blocked):
            task = asyncio.create_task(self.pool.needs_edit_batch_recovery(self.source_root))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        lease = await self.capture.gate.acquire(timeout_s=.2)
        await lease.release()
