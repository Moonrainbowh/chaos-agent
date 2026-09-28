from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Generic, TypeVar

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest
from code_agent.sessions.rewind_models import (
    RewindBaseline, RewindCoverageState, RewindGapPrepare,
    RewindMutationPath, RewindMutationPrepare,
)
from code_agent.sessions.rewind_repository import RewindSessionRepository
from code_agent.workspace.edits import EditPlan, WorkspaceEditor
from code_agent.workspace.rewind_state import (
    PreparedEditState, WorkspaceFileState, observe_file_states,
    prepare_edit_state,
)
from code_agent.workspace.snapshot_store import WorkspaceSnapshotStore
from code_agent_win.rewind_gate import WorkspaceMutationGate


_Result = TypeVar("_Result")


@dataclass(frozen=True)
class _Settled(Generic[_Result]):
    value: _Result | None = None
    error: BaseException | None = None
    cancellation: asyncio.CancelledError | None = None


class RewindCaptureCoordinator:
    """Capture inverse facts around approved workspace side effects."""

    def __init__(
        self,
        sessions: RewindSessionRepository,
        editor: WorkspaceEditor,
        snapshots: WorkspaceSnapshotStore,
        gate: WorkspaceMutationGate,
        *,
        existing_baseline: RewindBaseline = RewindBaseline.UNKNOWN,
    ) -> None:
        if not isinstance(existing_baseline, RewindBaseline):
            raise TypeError("existing_baseline must be a RewindBaseline")
        self.sessions, self.editor = sessions, editor
        self.snapshots, self.gate = snapshots, gate
        self.existing_baseline = existing_baseline
        self.workspace_fingerprint = snapshots.workspace_fingerprint

    async def apply_edit(
        self,
        context: ActionExecutionContext,
        request: ActionRequest,
        plan: EditPlan,
    ) -> None:
        _validate_identity(context, request)
        if type(plan) is not EditPlan:
            raise TypeError("plan must be an EditPlan")
        lease = await self.gate.acquire()
        try:
            coverage = await self._ensure()
            if coverage.state is RewindCoverageState.INVALIDATED:
                await self._apply_invalidated(context, request, plan, coverage.token)
                return
            prepared = await _worker(prepare_edit_state, self.editor, plan)
            handle = await _worker(self.snapshots.save, prepared.snapshot)
            mutation = await self._prepare_or_abort(_mutation_request(
                context, request, coverage.token, prepared, handle.to_dict(),
                self.existing_baseline,
            ), prepared)
            failure = await self._apply_worker(plan)
            if failure is not None:
                await self._reconcile(prepared, mutation.mutation_id)
                raise failure
            observation = await _observe_settled(
                self.editor, prepared.before.relative_path)
            if observation.cancellation is not None:
                await self._reconcile(
                    prepared, mutation.mutation_id, observation.value)
            observed = _raise_settled(observation)
            if observed != prepared.after:
                raise RuntimeError("workspace postimage does not match the edit plan")
            _raise_settled(await _ordered(
                self.sessions.complete_rewind_mutation(mutation.mutation_id)))
        finally:
            await lease.release()

    async def record_gap(
        self, context: ActionExecutionContext, request: ActionRequest, reason: str,
    ) -> None:
        _validate_identity(context, request)
        lease = await self.gate.acquire()
        try:
            coverage = await self._ensure()
            gap = _gap_request(context, request, coverage.token, reason)
            outcome = await _ordered(self.sessions.record_rewind_gap(gap))
            _raise_settled(outcome)
        finally:
            await lease.release()

    async def apply_edit_plan(
        self,
        context: ActionExecutionContext,
        request: ActionRequest,
        plan: object,
        stored_plan_id: str,
        cancellation: CancellationToken | None = None,
    ) -> object:
        _validate_identity(context, request)
        from code_agent_win.rewind_edit_batch import apply_edit_plan

        return await apply_edit_plan(
            self, context, request, plan, stored_plan_id, cancellation
        )

    async def recover_edit_batches(self) -> tuple[object, ...]:
        from code_agent_win.rewind_edit_batch import recover_edit_batches

        return await recover_edit_batches(self)

    async def _ensure(self) -> object:
        list_unresolved = getattr(
            self.sessions, "list_unresolved_edit_batches", None
        )
        if callable(list_unresolved):
            unresolved = _raise_settled(await _ordered(
                list_unresolved(self.workspace_fingerprint)
            ))
            if unresolved:
                raise RuntimeError("workspace has an unresolved edit batch")
        outcome = await _ordered(self.sessions.ensure_rewind_coverage(
            self.workspace_fingerprint))
        return _raise_settled(outcome)

    async def _apply_invalidated(
        self,
        context: ActionExecutionContext,
        request: ActionRequest,
        plan: EditPlan,
        coverage: object,
    ) -> None:
        gap = _gap_request(
            context, request, coverage, "coverage-already-invalidated")
        _raise_settled(await _ordered(self.sessions.record_rewind_gap(gap)))
        failure = await self._apply_worker(plan)
        if failure is not None:
            raise failure

    async def _prepare_or_abort(
        self, request: RewindMutationPrepare, prepared: PreparedEditState,
    ) -> object:
        outcome = await _ordered(self.sessions.prepare_rewind_mutation(request))
        if outcome.cancellation is None:
            return _raise_settled(outcome)
        if outcome.value is not None:
            cleanup = await _settle_task(asyncio.create_task(
                self._abort_prepared(prepared, outcome.value.mutation_id)))
            if cleanup.error is not None:
                raise cleanup.error
        raise outcome.cancellation

    async def _abort_prepared(
        self, prepared: PreparedEditState, mutation_id: str,
    ) -> None:
        observed = _raise_settled(
            await _observe_settled(self.editor, prepared.before.relative_path))
        if observed != prepared.before:
            raise RuntimeError("workspace changed during cancelled prepare")
        _raise_settled(await _ordered(
            self.sessions.abort_rewind_mutation(mutation_id)))

    async def _apply_worker(self, plan: EditPlan) -> BaseException | None:
        outcome = await _settle_task(
            asyncio.create_task(asyncio.to_thread(self.editor.apply, plan))
        )
        if outcome.cancellation is not None:
            return outcome.cancellation
        return outcome.error

    async def _reconcile(
        self, prepared: PreparedEditState, mutation_id: str,
        observed: WorkspaceFileState | None = None,
    ) -> None:
        try:
            if observed is None:
                observed = (await _observe_settled(
                    self.editor, prepared.before.relative_path)).value
            if observed == prepared.before:
                await _ordered(self.sessions.abort_rewind_mutation(mutation_id))
            elif observed == prepared.after:
                await _ordered(self.sessions.complete_rewind_mutation(mutation_id))
        except BaseException:
            return


def _validate_identity(
    context: ActionExecutionContext, request: ActionRequest,
) -> None:
    if type(context) is not ActionExecutionContext:
        raise TypeError("context must be an ActionExecutionContext")
    if type(request) is not ActionRequest:
        raise TypeError("request must be an ActionRequest")
    if context.request_id != request.id:
        raise ValueError("execution context request_id does not match request")


def _mutation_request(
    context: ActionExecutionContext, request: ActionRequest, coverage: object,
    prepared: PreparedEditState, handle: dict[str, object],
    existing_baseline: RewindBaseline,
) -> RewindMutationPrepare:
    before, after = prepared.before, prepared.after
    baseline = (
        RewindBaseline.ABSENT if not before.existed else existing_baseline
    )
    path = RewindMutationPath(
        before.relative_path, before.existed, before.sha256, baseline,
        after.existed, after.sha256,
    )
    return RewindMutationPrepare(
        coverage, context.owner_thread_id, context.origin_thread_id,
        context.task_id, context.parent_request_id, context.request_id,
        request.name, handle, (path,),
    )


def _gap_request(
    context: ActionExecutionContext, request: ActionRequest,
    coverage: object, reason: str,
) -> RewindGapPrepare:
    return RewindGapPrepare(
        coverage, context.owner_thread_id, context.origin_thread_id,
        context.task_id, context.parent_request_id, context.request_id,
        request.name, reason,
    )


async def _observe_settled(
    editor: object, path: str,
) -> _Settled[WorkspaceFileState]:
    outcome = await _settle_task(asyncio.create_task(
        asyncio.to_thread(observe_file_states, editor, (path,))
    ))
    states = outcome.value
    if states is None:
        return _Settled(error=outcome.error, cancellation=outcome.cancellation)
    if type(states) is not tuple or len(states) != 1:
        return _Settled(
            error=RuntimeError("workspace observation is incomplete"),
            cancellation=outcome.cancellation,
        )
    return _Settled(states[0], outcome.error, outcome.cancellation)


async def _worker(function: object, *args: object) -> object:
    outcome = await _settle_task(
        asyncio.create_task(asyncio.to_thread(function, *args))
    )
    return _raise_settled(outcome)


async def _ordered(awaitable: Awaitable[_Result]) -> _Settled[_Result]:
    return await _settle_task(asyncio.create_task(awaitable))


async def _settle_task(task: asyncio.Task[_Result]) -> _Settled[_Result]:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except BaseException:
            break
    try:
        return _Settled(task.result(), cancellation=cancellation)
    except BaseException as error:
        return _Settled(error=error, cancellation=cancellation)


def _raise_settled(outcome: _Settled[_Result]) -> _Result:
    if outcome.cancellation is not None:
        raise outcome.cancellation
    if outcome.error is not None:
        raise outcome.error
    return outcome.value  # type: ignore[return-value]
