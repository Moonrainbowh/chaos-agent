from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.models import ActionRequest
from code_agent.sessions.edit_batch_models import (
    EditBatchOperation,
    EditBatchOperationKind,
    EditBatchPath,
    EditBatchPrepare,
    EditBatchState,
)
from code_agent.sessions.rewind_models import (
    RewindBaseline,
    RewindMutationPath,
    RewindMutationPrepare,
)
from code_agent.sessions.rewind_repository import RewindSessionRepository
from code_agent.workspace import _batch_apply
from code_agent.workspace.edits import (
    BatchApplyStatus,
    RecoveryOperation,
    RecoveryOperationKind,
    WorkspaceEditor,
)
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.snapshot_store import WorkspaceSnapshotStore
from code_agent_win.rewind_capture import RewindCaptureCoordinator


class _Lease:
    async def release(self) -> None:
        return None


class _Gate:
    async def acquire(self) -> _Lease:
        return _Lease()


class RewindBatchCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / "workspace"
        self.root.mkdir()
        self.editor = WorkspaceEditor(WorkspacePathGuard(self.root))
        self.snapshots = WorkspaceSnapshotStore(
            self.editor.guard, base / "product-state" / "rewind-snapshots"
        )
        self.sessions = RewindSessionRepository(base / "sessions.sqlite3")
        self.owner = await self.sessions.create_thread()
        self.capture = RewindCaptureCoordinator(
            self.sessions, self.editor, self.snapshots, _Gate()
        )

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    def _identity(self, request_id: str) -> tuple[ActionExecutionContext, ActionRequest]:
        return (
            ActionExecutionContext(self.owner, self.owner, request_id),
            ActionRequest(request_id, "apply_workspace_edit_plan_v1", {}),
        )

    def _representative_plan(self):
        (self.root / "update.txt").write_bytes(b"update-before")
        (self.root / "delete.txt").write_bytes(b"delete-before")
        (self.root / "move.txt").write_bytes(b"move-before")
        return self.editor.plan_batch(
            (
                self.editor.plan_write("create.txt", "create-after"),
                self.editor.plan_write("update.txt", "update-after"),
                self.editor.plan_delete("delete.txt"),
                self.editor.plan_move("move.txt", "moved.txt"),
            )
        )

    async def test_success_is_journaled_before_writes_and_completed(self) -> None:
        plan = self._representative_plan()
        context, request = self._identity("success")

        result = await self.capture.apply_edit_plan(
            context, request, plan, "stored-success"
        )

        self.assertEqual(result.status, BatchApplyStatus.APPLIED)
        records = await self.sessions.list_edit_batches(
            self.snapshots.workspace_fingerprint
        )
        self.assertEqual(records[0].state, EditBatchState.COMPLETED)
        self.assertTrue(all(item.committed_at for item in records[0].operations))
        self.assertEqual((self.root / "update.txt").read_bytes(), b"update-after")
        self.assertFalse((self.root / "delete.txt").exists())
        self.assertEqual((self.root / "moved.txt").read_bytes(), b"move-before")

    async def test_mid_batch_failure_is_rolled_back_and_settled(self) -> None:
        plan = self._representative_plan()
        context, request = self._identity("rollback")
        execute = _batch_apply._execute_operation
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected")
            return execute(*args, **kwargs)

        with patch.object(_batch_apply, "_execute_operation", side_effect=fail_second):
            result = await self.capture.apply_edit_plan(
                context, request, plan, "stored-rollback"
            )

        self.assertEqual(result.status, BatchApplyStatus.ROLLED_BACK)
        records = await self.sessions.list_edit_batches(
            self.snapshots.workspace_fingerprint
        )
        self.assertEqual(records[0].state, EditBatchState.ROLLED_BACK)
        self.assertFalse((self.root / "create.txt").exists())
        self.assertEqual((self.root / "update.txt").read_bytes(), b"update-before")

    async def test_startup_recovery_rolls_back_durable_postimages(self) -> None:
        plan = self._representative_plan()
        await self._leave_applying(plan, "crash")
        self.assertEqual(self.editor.apply_batch(plan).status, BatchApplyStatus.APPLIED)

        results = await self.capture.recover_edit_batches()

        self.assertEqual(results[0].status, BatchApplyStatus.ROLLED_BACK)
        record = (await self.sessions.list_edit_batches(
            self.snapshots.workspace_fingerprint
        ))[0]
        self.assertEqual(record.state, EditBatchState.ROLLED_BACK)
        self.assertFalse((self.root / "create.txt").exists())
        self.assertEqual((self.root / "update.txt").read_bytes(), b"update-before")
        self.assertEqual((self.root / "move.txt").read_bytes(), b"move-before")

    async def test_recovery_foreign_state_performs_zero_writes_and_conflicts(self) -> None:
        (self.root / "first.txt").write_bytes(b"first-before")
        (self.root / "second.txt").write_bytes(b"second-before")
        plan = self.editor.plan_batch(
            (
                self.editor.plan_write("first.txt", "first-after"),
                self.editor.plan_write("second.txt", "second-after"),
            )
        )
        await self._leave_applying(plan, "foreign")
        self.assertEqual(self.editor.apply_batch(plan).status, BatchApplyStatus.APPLIED)
        (self.root / "second.txt").write_bytes(b"user-change")

        results = await self.capture.recover_edit_batches()

        self.assertEqual(results[0].status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual((self.root / "first.txt").read_bytes(), b"first-after")
        self.assertEqual((self.root / "second.txt").read_bytes(), b"user-change")
        record = (await self.sessions.list_unresolved_edit_batches(
            self.snapshots.workspace_fingerprint
        ))[0]
        self.assertEqual(record.state, EditBatchState.CONFLICTED)

    async def test_token_cancellation_during_worker_rolls_back_before_gate_release(self) -> None:
        plan = self._representative_plan()
        context, request = self._identity("cancel-worker")
        cancellation = CancellationToken()
        started = threading.Event()
        finish = threading.Event()
        real_apply = self.editor.apply_batch

        def blocked_apply(batch):
            started.set()
            finish.wait(2)
            return real_apply(batch)

        with patch.object(self.editor, "apply_batch", side_effect=blocked_apply):
            task = asyncio.create_task(
                self.capture.apply_edit_plan(
                    context, request, plan, "stored-cancel-worker", cancellation
                )
            )
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            cancellation.cancel("user paused")
            finish.set()
            with self.assertRaises(CancellationError):
                await task

        self.assertFalse((self.root / "create.txt").exists())
        self.assertEqual((self.root / "update.txt").read_bytes(), b"update-before")
        record = (await self.sessions.list_edit_batches(
            self.snapshots.workspace_fingerprint
        ))[0]
        self.assertEqual(record.state, EditBatchState.ROLLED_BACK)

    async def _leave_applying(self, plan, request_id: str) -> None:
        prepared = self.editor.preflight_batch(plan)
        snapshot = self.editor.snapshot_from_prepared(prepared)
        handle = self.snapshots.save(snapshot)
        recovery = self.editor.recovery_operations_from_prepared(prepared)
        operations = tuple(_journal_operation(item) for item in recovery)
        mutation_paths = tuple(
            _mutation_path(endpoint)
            for operation in operations
            for endpoint in _parent_endpoints(operation)
        )
        coverage = await self.sessions.ensure_rewind_coverage(
            self.snapshots.workspace_fingerprint
        )
        mutation = RewindMutationPrepare(
            coverage.token,
            self.owner,
            self.owner,
            None,
            None,
            request_id,
            "apply_workspace_edit_plan_v1",
            handle.to_dict(),
            mutation_paths,
        )
        record = await self.sessions.prepare_edit_batch(
            EditBatchPrepare(mutation, f"stored-{request_id}", plan.plan_id, operations)
        )
        await self.sessions.transition_edit_batch(
            record.mutation.mutation_id, EditBatchState.APPLYING
        )


def _journal_operation(operation: RecoveryOperation) -> EditBatchOperation:
    kinds = {
        RecoveryOperationKind.CREATE: EditBatchOperationKind.CREATE,
        RecoveryOperationKind.UPDATE: EditBatchOperationKind.WRITE,
        RecoveryOperationKind.DELETE: EditBatchOperationKind.DELETE,
        RecoveryOperationKind.MOVE: EditBatchOperationKind.MOVE,
    }
    source = _endpoint(operation.source) if operation.destination is not None else None
    target = _endpoint(operation.destination or operation.source)
    return EditBatchOperation(kinds[operation.kind], source, target, operation.case_only)


def _endpoint(transition) -> EditBatchPath:
    return EditBatchPath(
        transition.before.relative_path,
        transition.before.existed,
        transition.before.sha256,
        transition.before.size,
        transition.after.existed,
        transition.after.sha256,
        transition.after.size,
    )


def _parent_endpoints(operation: EditBatchOperation) -> tuple[EditBatchPath, ...]:
    if operation.case_only:
        assert operation.source is not None
        return (
            EditBatchPath(
                operation.source.path,
                True,
                operation.source.before_sha256,
                operation.source.before_size,
                True,
                operation.source.before_sha256,
                operation.source.before_size,
            ),
        )
    return tuple(item for item in (operation.source, operation.target) if item is not None)


def _mutation_path(endpoint: EditBatchPath) -> RewindMutationPath:
    baseline = RewindBaseline.UNKNOWN if endpoint.before_existed else RewindBaseline.ABSENT
    return RewindMutationPath(
        endpoint.path,
        endpoint.before_existed,
        endpoint.before_sha256,
        baseline,
        endpoint.after_existed,
        endpoint.after_sha256,
    )


if __name__ == "__main__":
    unittest.main()
