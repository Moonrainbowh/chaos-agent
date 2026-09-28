from __future__ import annotations

from code_agent.core.models import ActionRequest, ActionResult
from code_agent.workspace._batch_models import BatchApplyResult, BatchApplyStatus

from code_agent_win.action_support import error_result, ok_result
from code_agent_win.edit_plan_store import (
    StoredPlanStatus,
    StoredWorkspaceEditPlan,
    WorkspaceEditPlanStore,
)


def mutation_result(
    result: ActionResult, stored: StoredWorkspaceEditPlan, may_have_changed: bool
) -> ActionResult:
    output = dict(result.output)
    output["workspace_may_have_changed"] = may_have_changed
    output["paths"] = [state.relative_path for state in stored.plan.paths]
    return ActionResult(
        result.request_id, result.name, output, result.is_error, result.metadata
    )


def apply_result(
    request: ActionRequest,
    stored: StoredWorkspaceEditPlan,
    result: BatchApplyResult,
    store: WorkspaceEditPlanStore,
    invalidate_cache: object | None,
) -> ActionResult:
    paths = tuple(state.relative_path for state in stored.plan.paths)
    if result.status is BatchApplyStatus.APPLIED:
        store.settle(stored.plan_id, StoredPlanStatus.APPLIED)
        if callable(invalidate_cache):
            invalidate_cache(paths)
        return ok_result(request, {
            "status": result.status.value,
            "operation_count": len(stored.plan.operations),
            "paths": list(paths),
            "workspace_may_have_changed": True,
        })
    partial = result.status is BatchApplyStatus.PARTIAL_CONFLICT
    store.settle(
        stored.plan_id,
        StoredPlanStatus.RECOVERY_REQUIRED if partial else StoredPlanStatus.CONFLICTED,
    )
    code = "apply_failed_partial_conflict" if partial else "apply_failed_rolled_back"
    base = error_result(request, result.status.value, result.error, error_code=code)
    output = dict(base.output)
    output["workspace_may_have_changed"] = partial
    output["paths"] = list(paths) if partial else []
    output["conflicts"] = [
        {"path": item.relative_path, "reason": item.reason}
        for item in result.conflicts
    ]
    return ActionResult(
        base.request_id, base.name, output, is_error=True, metadata=base.metadata
    )


__all__ = ["apply_result", "mutation_result"]
