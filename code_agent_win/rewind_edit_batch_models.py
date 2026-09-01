from __future__ import annotations

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.models import ActionRequest
from code_agent.sessions.edit_batch_models import (
    EditBatchOperation,
    EditBatchOperationKind,
    EditBatchPath,
    EditBatchPrepare,
)
from code_agent.sessions.rewind_models import (
    RewindBaseline,
    RewindMutationPath,
    RewindMutationPrepare,
)
from code_agent.workspace.edits import (
    PathTransition,
    PreparedBatchEdit,
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryPathState,
)
from code_agent.workspace.snapshot_store import SnapshotHandle


def prepare_request(
    capture: object,
    context: ActionExecutionContext,
    request: ActionRequest,
    prepared: PreparedBatchEdit,
    handle: SnapshotHandle,
    stored_plan_id: str,
    coverage: object,
) -> EditBatchPrepare:
    recovery = capture.editor.recovery_operations_from_prepared(prepared)
    operations = tuple(_batch_operation(item) for item in recovery)
    paths = tuple(
        _mutation_path(endpoint, capture.existing_baseline)
        for operation in operations
        for endpoint in _parent_endpoints(operation)
    )
    mutation = RewindMutationPrepare(
        coverage,
        context.owner_thread_id,
        context.origin_thread_id,
        context.task_id,
        context.parent_request_id,
        context.request_id,
        request.name,
        handle.to_dict(),
        paths,
    )
    return EditBatchPrepare(
        mutation, stored_plan_id, prepared.plan.plan_id, operations
    )


def recovery_operation(operation: EditBatchOperation) -> RecoveryOperation:
    kinds = {
        EditBatchOperationKind.CREATE: RecoveryOperationKind.CREATE,
        EditBatchOperationKind.WRITE: RecoveryOperationKind.UPDATE,
        EditBatchOperationKind.DELETE: RecoveryOperationKind.DELETE,
        EditBatchOperationKind.MOVE: RecoveryOperationKind.MOVE,
    }
    source = _transition(operation.source or operation.target)
    destination = _transition(operation.target) if operation.source else None
    return RecoveryOperation(
        kinds[operation.kind], source, destination, operation.case_only
    )


def thaw(value: object) -> object:
    if value is None:
        raise ValueError("edit batch snapshot handle is missing")
    if isinstance(value, dict) or hasattr(value, "items"):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


def _batch_operation(operation: RecoveryOperation) -> EditBatchOperation:
    kinds = {
        RecoveryOperationKind.CREATE: EditBatchOperationKind.CREATE,
        RecoveryOperationKind.UPDATE: EditBatchOperationKind.WRITE,
        RecoveryOperationKind.DELETE: EditBatchOperationKind.DELETE,
        RecoveryOperationKind.MOVE: EditBatchOperationKind.MOVE,
    }
    source = _batch_path(operation.source) if operation.destination else None
    target = _batch_path(operation.destination or operation.source)
    return EditBatchOperation(kinds[operation.kind], source, target, operation.case_only)


def _batch_path(transition: PathTransition) -> EditBatchPath:
    before, after = transition.before, transition.after
    return EditBatchPath(
        before.relative_path,
        before.existed,
        before.sha256,
        before.size,
        after.existed,
        after.sha256,
        after.size,
    )


def _parent_endpoints(operation: EditBatchOperation) -> tuple[EditBatchPath, ...]:
    if operation.case_only:
        assert operation.source is not None
        source = operation.source
        return (
            EditBatchPath(
                source.path,
                True,
                source.before_sha256,
                source.before_size,
                True,
                source.before_sha256,
                source.before_size,
            ),
        )
    return tuple(item for item in (operation.source, operation.target) if item)


def _mutation_path(
    endpoint: EditBatchPath, existing_baseline: RewindBaseline
) -> RewindMutationPath:
    baseline = existing_baseline if endpoint.before_existed else RewindBaseline.ABSENT
    return RewindMutationPath(
        endpoint.path,
        endpoint.before_existed,
        endpoint.before_sha256,
        baseline,
        endpoint.after_existed,
        endpoint.after_sha256,
    )


def _transition(endpoint: EditBatchPath) -> PathTransition:
    return PathTransition(
        RecoveryPathState(
            endpoint.path,
            endpoint.before_existed,
            endpoint.before_sha256,
            endpoint.before_size,
        ),
        RecoveryPathState(
            endpoint.path,
            endpoint.after_existed,
            endpoint.after_sha256,
            endpoint.after_size,
        ),
    )


__all__ = ["prepare_request", "recovery_operation", "thaw"]
