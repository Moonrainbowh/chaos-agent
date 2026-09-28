from __future__ import annotations

import hashlib
import os

from code_agent.interfaces.approval import (
    MAX_EDIT_PLAN_APPROVAL_DIFF_CHARS,
    EditPlanApprovalView,
)
from code_agent.workspace._batch_models import BatchEditPlan, DeletePlan, MovePlan
from code_agent.workspace._edit_plan import EditPlan
from code_agent.workspace.edits import WorkspaceEditor

from code_agent_win.edit_plan_store import StoredWorkspaceEditPlan


def default_workspace_fingerprint(editor: WorkspaceEditor) -> str:
    path = os.path.normcase(str(editor.guard.root)).encode("utf-8")
    native = ":".join(str(value) for value in editor.guard.root_identity).encode("ascii")
    payload = b"workspace-snapshot-v1\0" + path + b"\0" + native
    return hashlib.sha256(payload).hexdigest()


def approval_view(stored: StoredWorkspaceEditPlan) -> EditPlanApprovalView:
    if not isinstance(stored.plan, BatchEditPlan):
        raise TypeError("stored edit plan has an invalid type")
    diff, truncated = bounded_diff(stored.plan.diff)
    return EditPlanApprovalView(
        stored.plan_id,
        stored.plan_digest,
        tuple(operation_summary(item) for item in stored.plan.operations),
        stored.risk_flags,
        tuple(state.relative_path for state in stored.plan.paths),
        diff,
        truncated,
    )


def operation_summary(operation: object) -> str:
    if type(operation) is EditPlan:
        return f"{'update' if operation.existed else 'create'} {operation.relative_path}"
    if type(operation) is DeletePlan:
        return f"delete {operation.source.relative_path}"
    if type(operation) is MovePlan:
        return f"move {operation.source.relative_path} -> {operation.destination.relative_path}"
    raise TypeError("stored edit operation has an invalid type")


def bounded_diff(value: str) -> tuple[str, bool]:
    maximum = MAX_EDIT_PLAN_APPROVAL_DIFF_CHARS
    return (value, False) if len(value) <= maximum else (value[:maximum], True)


__all__ = [
    "approval_view",
    "bounded_diff",
    "default_workspace_fingerprint",
    "operation_summary",
]
