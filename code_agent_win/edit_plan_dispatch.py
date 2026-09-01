from __future__ import annotations

import asyncio
from dataclasses import dataclass

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.models import ActionRequest, ActionResult
from code_agent.interfaces.approval import EditPlanApprovalView
from code_agent.policy.classifier import requires_explicit_edit_plan_approval
from code_agent.workspace._batch_models import (
    BatchApplyResult,
    BatchEditPlan,
)
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.errors import (
    BatchEditConflictError,
    CrossVolumeMoveError,
)

from code_agent_win.action_support import error_result, ok_result
from code_agent_win.edit_plan_store import (
    EditPlanStoreError,
    StoredPlanStatus,
    StoredWorkspaceEditPlan,
    WorkspaceEditPlanStore,
)
from code_agent_win.edit_plan_preview import approval_view, bounded_diff
from code_agent_win.edit_plan_results import apply_result, mutation_result
from code_agent_win.workspace_edit_planning import (
    WorkspaceEditPlanningError,
    create_stored_edit_plan,
)


@dataclass(frozen=True)
class EditPlanAuthorization:
    risk_flags: tuple[str, ...] = ()
    view: EditPlanApprovalView | None = None


class WorkspaceEditPlanActions:
    """Execute the two trusted edit-plan tools for one workspace."""

    def __init__(
        self,
        editor: WorkspaceEditor,
        store: WorkspaceEditPlanStore,
        workspace_fingerprint: str,
        *,
        git: object | None = None,
        capture: object | None = None,
        invalidate_cache: object | None = None,
    ) -> None:
        self.editor, self.store = editor, store
        self.workspace_fingerprint = workspace_fingerprint
        self.git, self.capture = git, capture
        self.invalidate_cache = invalidate_cache

    def authorization(
        self, request: ActionRequest, context: ActionExecutionContext | None
    ) -> EditPlanAuthorization:
        if request.name != "apply_workspace_edit_plan_v1":
            return EditPlanAuthorization()
        owner, task = _owner(context, request)
        stored = self.store.require_applicable(
            _argument(request, "plan_id"),
            _argument(request, "plan_digest"),
            workspace_fingerprint=self.workspace_fingerprint,
            owner_thread_id=owner,
            task_id=task,
        )
        return EditPlanAuthorization(stored.risk_flags, approval_view(stored))

    async def execute(
        self,
        request: ActionRequest,
        context: ActionExecutionContext | None,
        cancellation: CancellationToken,
    ) -> ActionResult | None:
        if request.name == "plan_workspace_edits_v1":
            return await self._plan(request, context)
        if request.name == "apply_workspace_edit_plan_v1":
            return await self._apply(request, context, cancellation)
        return None

    async def _plan(
        self, request: ActionRequest, context: ActionExecutionContext | None
    ) -> ActionResult:
        owner, task = _owner(context, request)
        operations = request.arguments.get("operations")
        if not isinstance(operations, (list, tuple)):
            return error_result(
                request, "invalid edit operation", error_code="invalid_edit_operation"
            )
        supersedes = request.arguments.get("supersedes_plan_id")
        try:
            planned = await asyncio.to_thread(
                create_stored_edit_plan,
                self.editor,
                self.store,
                tuple(operations),
                workspace_fingerprint=self.workspace_fingerprint,
                owner_thread_id=owner,
                task_id=task,
                git=self.git,
                supersedes_plan_id=supersedes,
            )
        except (WorkspaceEditPlanningError, EditPlanStoreError) as error:
            return error_result(
                request, "edit planning failed", str(error), error_code=error.code
            )
        except CrossVolumeMoveError as error:
            return error_result(
                request,
                "cross-volume move is unsupported",
                str(error),
                error_code="cross_volume_move_unsupported",
            )
        diff, truncated = bounded_diff(planned.combined_diff)
        output = {
            "status": "planned",
            "plan_id": planned.plan_id,
            "plan_digest": planned.plan_digest,
            "operation_count": planned.operation_count,
            "path_count": planned.path_count,
            "operations": list(planned.operation_summaries),
            "risk_flags": list(planned.risk_flags),
            "dirty_paths": list(planned.dirty_paths),
            "combined_diff": diff,
            "diff_truncated": truncated,
            "requires_confirmation": requires_explicit_edit_plan_approval(
                planned.risk_flags
            ),
        }
        return ok_result(request, output, {"diff": diff})

    async def _apply(
        self,
        request: ActionRequest,
        context: ActionExecutionContext | None,
        cancellation: CancellationToken,
    ) -> ActionResult:
        owner, task = _owner(context, request)
        stored: StoredWorkspaceEditPlan | None = None
        apply_started = False
        try:
            stored = self.store.require_applicable(
                _argument(request, "plan_id"),
                _argument(request, "plan_digest"),
                workspace_fingerprint=self.workspace_fingerprint,
                owner_thread_id=owner,
                task_id=task,
            )
            cancellation.raise_if_cancelled()
            self.store.begin_apply(stored.plan_id)
            apply_started = True
            result = await self._apply_stored(
                context, request, stored, cancellation
            )
            return apply_result(
                request, stored, result, self.store, self.invalidate_cache
            )
        except EditPlanStoreError as error:
            return error_result(
                request, "edit plan is not applicable", str(error), error_code=error.code
            )
        except BatchEditConflictError as error:
            self._settle_if_applying(request, StoredPlanStatus.CONFLICTED)
            return error_result(
                request, "edit plan conflict", str(error), error_code="edit_plan_conflict"
            )
        except CrossVolumeMoveError as error:
            self._settle_if_applying(request, StoredPlanStatus.CONFLICTED)
            return error_result(
                request,
                "cross-volume move is unsupported",
                str(error),
                error_code="cross_volume_move_unsupported",
            )
        except CancellationError:
            self._settle_if_applying(
                request, StoredPlanStatus.RECOVERY_REQUIRED
            )
            raise
        except asyncio.CancelledError:
            self._settle_if_applying(request, StoredPlanStatus.RECOVERY_REQUIRED)
            raise
        except Exception as error:
            self._settle_if_applying(request, StoredPlanStatus.RECOVERY_REQUIRED)
            failed = error_result(
                request,
                "edit apply requires recovery",
                type(error).__name__,
                error_code="recovery_required",
            )
            return (
                mutation_result(failed, stored, True)
                if apply_started and stored is not None
                else failed
            )
    async def _apply_stored(
        self,
        context: ActionExecutionContext | None,
        request: ActionRequest,
        stored: StoredWorkspaceEditPlan,
        cancellation: CancellationToken,
    ) -> BatchApplyResult:
        if not isinstance(stored.plan, BatchEditPlan):
            raise TypeError("stored edit plan has an invalid type")
        if self.capture is not None:
            method = getattr(self.capture, "apply_edit_plan", None)
            if not callable(method):
                raise RuntimeError("batch rewind capture is unavailable")
            return await method(
                context,
                request,
                stored.plan,
                stored.plan_id,
                cancellation,
            )
        return await asyncio.to_thread(self.editor.apply_batch, stored.plan)

    def _settle_if_applying(
        self, request: ActionRequest, status: StoredPlanStatus
    ) -> None:
        plan_id = request.arguments.get("plan_id")
        if not isinstance(plan_id, str):
            return
        try:
            if self.store.get(plan_id).status is StoredPlanStatus.APPLYING:
                self.store.settle(plan_id, status)
        except (EditPlanStoreError, ValueError):
            return


def _owner(
    context: ActionExecutionContext | None, request: ActionRequest
) -> tuple[str, str | None]:
    if not isinstance(context, ActionExecutionContext):
        raise TypeError("execution_context is required for edit plans")
    if context.request_id != request.id:
        raise ValueError("execution context request_id does not match request")
    return context.owner_thread_id, context.task_id


def _argument(request: ActionRequest, name: str) -> str:
    value = request.arguments.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


__all__ = [
    "EditPlanAuthorization",
    "WorkspaceEditPlanActions",
]
