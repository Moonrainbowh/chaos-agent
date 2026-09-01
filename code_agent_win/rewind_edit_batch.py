from __future__ import annotations

import asyncio

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.models import ActionRequest
from code_agent.sessions.edit_batch_models import (
    EditBatchRecord,
    EditBatchState,
)
from code_agent.sessions.errors import SessionStorageError
from code_agent.sessions.rewind_models import RewindCoverageState
from code_agent.workspace.edits import (
    BatchApplyResult,
    BatchApplyStatus,
    BatchEditPlan,
)
from code_agent.workspace.snapshot_store import SnapshotHandle

from code_agent_win.rewind_edit_async import (
    ordered,
    take,
    thread,
    thread_until_token,
    value,
)
from code_agent_win.rewind_edit_batch_models import (
    prepare_request,
    recovery_operation,
    thaw,
)


async def apply_edit_plan(
    capture: object,
    context: ActionExecutionContext,
    request: ActionRequest,
    plan: BatchEditPlan,
    stored_plan_id: str,
    cancellation: CancellationToken | None = None,
) -> BatchApplyResult:
    if type(plan) is not BatchEditPlan:
        raise TypeError("plan must be a BatchEditPlan")
    if not isinstance(stored_plan_id, str) or not stored_plan_id.strip():
        raise ValueError("stored_plan_id must be non-blank text")
    if cancellation is not None and not isinstance(cancellation, CancellationToken):
        raise TypeError("cancellation must be a CancellationToken or None")
    _check(cancellation)
    lease = await capture.gate.acquire()
    record: EditBatchRecord | None = None
    try:
        _check(cancellation)
        unresolved = await value(await ordered(
            capture.sessions.list_unresolved_edit_batches(
                capture.workspace_fingerprint
            )
        ))
        _check(cancellation)
        if unresolved:
            raise SessionStorageError("workspace has an unresolved edit batch")
        coverage = await value(await ordered(
            capture.sessions.ensure_rewind_coverage(
                capture.workspace_fingerprint
            )
        ))
        _check(cancellation)
        if coverage.state is not RewindCoverageState.ACTIVE:
            raise SessionStorageError("rewind coverage is not active")
        prepared = await value(await thread(capture.editor.preflight_batch, plan))
        _check(cancellation)
        snapshot = await value(await thread(
            capture.editor.snapshot_from_prepared, prepared
        ))
        _check(cancellation)
        handle = await value(await thread(capture.snapshots.save, snapshot))
        _check(cancellation)
        request_model = prepare_request(
            capture, context, request, prepared, handle, stored_plan_id, coverage.token
        )
        prepared_outcome = await ordered(
            capture.sessions.prepare_edit_batch(request_model)
        )
        if prepared_outcome.error is not None:
            raise prepared_outcome.error
        record = prepared_outcome.value
        assert record is not None
        if prepared_outcome.cancellation is not None:
            raise prepared_outcome.cancellation
        _check(cancellation)
        await value(await ordered(capture.sessions.transition_edit_batch(
            record.mutation.mutation_id, EditBatchState.APPLYING
        )))
        _check(cancellation)
        applied = await thread_until_token(
            capture.editor.apply_batch,
            plan,
            cancellation=cancellation,
        )
        if applied.error is not None:
            raise applied.error
        result = applied.value
        if not isinstance(result, BatchApplyResult):
            raise TypeError("apply_batch must return BatchApplyResult")
        if applied.cancellation is not None:
            raise applied.cancellation
        _check(cancellation)
        await _persist_apply_result(capture.sessions, record, result, cancellation)
        return result
    except BaseException as primary:
        recovered: BatchApplyResult | None = None
        if record is not None:
            try:
                recovered = await _recover_record(capture, record)
            except BaseException as recovery_error:
                add_note = getattr(primary, "add_note", None)
                if callable(add_note):
                    add_note(f"edit batch recovery failed: {recovery_error}")
        if (
            recovered is not None
            and recovered.status is BatchApplyStatus.APPLIED
            and isinstance(primary, (CancellationError, asyncio.CancelledError))
        ):
            return recovered
        raise
    finally:
        await lease.release()


async def recover_edit_batches(capture: object) -> tuple[BatchApplyResult, ...]:
    lease = await capture.gate.acquire()
    try:
        records = await value(await ordered(
            capture.sessions.list_unresolved_edit_batches(
                capture.workspace_fingerprint
            )
        ))
        results: list[BatchApplyResult] = []
        for record in records:
            results.append(await _recover_record(capture, record))
        return tuple(results)
    finally:
        await lease.release()


async def _persist_apply_result(
    sessions: object,
    record: EditBatchRecord,
    result: BatchApplyResult,
    cancellation: CancellationToken | None,
) -> None:
    mutation_id = record.mutation.mutation_id
    if result.status is BatchApplyStatus.APPLIED:
        expected = tuple(range(len(record.operations)))
        if result.applied_operations != expected:
            raise RuntimeError("applied batch progress is incomplete")
        for ordinal in expected:
            _check(cancellation)
            await value(await ordered(
                sessions.commit_edit_batch_operation(mutation_id, ordinal)
            ))
            _check(cancellation)
        await value(await ordered(sessions.settle_edit_batch(
            mutation_id, EditBatchState.COMPLETED
        )))
        return
    _check(cancellation)
    if result.status is BatchApplyStatus.ROLLED_BACK:
        await value(await ordered(sessions.settle_edit_batch(
            mutation_id, EditBatchState.ROLLED_BACK
        )))
        return
    await value(await ordered(sessions.settle_edit_batch(
        mutation_id,
        EditBatchState.CONFLICTED,
        conflict_code="workspace-drift",
    )))


async def _recover_record(
    capture: object, record: EditBatchRecord
) -> BatchApplyResult:
    pending: asyncio.CancelledError | None = None
    current, pending = take(
        await ordered(capture.sessions.get_edit_batch(record.mutation.mutation_id)),
        pending,
    )
    if current.state is EditBatchState.COMPLETED:
        return _finish_recovery(BatchApplyResult(BatchApplyStatus.APPLIED), pending)
    if current.state is EditBatchState.ROLLED_BACK:
        return _finish_recovery(BatchApplyResult(BatchApplyStatus.ROLLED_BACK), pending)
    if current.state is EditBatchState.CONFLICTED:
        return _finish_recovery(
            BatchApplyResult(
                BatchApplyStatus.PARTIAL_CONFLICT,
                error=current.conflict_code,
            ),
            pending,
        )
    current, pending = take(
        await ordered(capture.sessions.transition_edit_batch(
            current.mutation.mutation_id, EditBatchState.ROLLING_BACK
        )),
        pending,
    )
    try:
        handle = SnapshotHandle.from_dict(thaw(current.mutation.snapshot_handle))
        snapshot, pending = take(await thread(capture.snapshots.load, handle), pending)
        operations = tuple(
            recovery_operation(item.operation) for item in current.operations
        )
        result, pending = take(
            await thread(capture.editor.recover_batch, operations, snapshot),
            pending,
        )
    except BaseException as primary:
        settled = await ordered(capture.sessions.settle_edit_batch(
            current.mutation.mutation_id,
            EditBatchState.CONFLICTED,
            conflict_code="recovery-error",
        ))
        if settled.error is not None:
            add_note = getattr(primary, "add_note", None)
            if callable(add_note):
                add_note(f"failed to persist recovery conflict: {settled.error}")
        raise
    target = (
        EditBatchState.ROLLED_BACK
        if result.status is BatchApplyStatus.ROLLED_BACK
        else EditBatchState.CONFLICTED
    )
    arguments = (
        {}
        if target is EditBatchState.ROLLED_BACK
        else {"conflict_code": "workspace-drift"}
    )
    _, pending = take(
        await ordered(capture.sessions.settle_edit_batch(
            current.mutation.mutation_id, target, **arguments
        )),
        pending,
    )
    return _finish_recovery(result, pending)


def _check(cancellation: CancellationToken | None) -> None:
    if cancellation is not None:
        cancellation.raise_if_cancelled()


def _finish_recovery(
    result: BatchApplyResult,
    cancellation: asyncio.CancelledError | None,
) -> BatchApplyResult:
    if cancellation is not None:
        raise cancellation
    return result


__all__ = ["apply_edit_plan", "recover_edit_batches"]
