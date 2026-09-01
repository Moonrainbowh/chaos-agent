from __future__ import annotations

from ._batch_apply import PreparedBatchEdit, _operation_paths, _post_states
from ._batch_models import (
    DeletePlan,
    MovePlan,
    PathTransition,
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryPathState,
)
from ._edit_plan import EditPlan
from ._secure_io import canonical_path_key
from .edits import SnapshotEntry, WorkspaceSnapshot


def snapshot_from_prepared(prepared: PreparedBatchEdit) -> WorkspaceSnapshot:
    if type(prepared) is not PreparedBatchEdit:
        raise TypeError("prepared must be a PreparedBatchEdit")
    observed = {
        item.state.relative_path: item for item in prepared.observations
    }
    entries: list[SnapshotEntry] = []
    seen: set[str] = set()
    for operation in prepared.plan.operations:
        paths = _operation_paths(operation)
        if type(operation) is MovePlan and operation.case_only:
            paths = paths[:1]
        for relative in paths:
            key = canonical_path_key(relative)
            if key in seen:
                continue
            seen.add(key)
            item = observed[relative]
            content = item.content if item.state.existed else None
            entries.append(SnapshotEntry(relative, content, item.state.existed))
    return WorkspaceSnapshot(tuple(entries))


def recovery_operations_from_prepared(
    prepared: PreparedBatchEdit,
) -> tuple[RecoveryOperation, ...]:
    if type(prepared) is not PreparedBatchEdit:
        raise TypeError("prepared must be a PreparedBatchEdit")
    observed = {
        item.state.relative_path: item for item in prepared.observations
    }
    recovery: list[RecoveryOperation] = []
    for operation in prepared.plan.operations:
        paths = _operation_paths(operation)
        before = tuple(observed[path] for path in paths)
        after = _post_states(operation, before)
        transitions = tuple(
            PathTransition(_recovery_state(old.state), _recovery_state(new))
            for old, new in zip(before, after)
        )
        if type(operation) is EditPlan:
            kind = (
                RecoveryOperationKind.UPDATE
                if operation.existed
                else RecoveryOperationKind.CREATE
            )
            recovery.append(RecoveryOperation(kind, transitions[0]))
        elif type(operation) is DeletePlan:
            recovery.append(
                RecoveryOperation(RecoveryOperationKind.DELETE, transitions[0])
            )
        else:
            assert type(operation) is MovePlan
            recovery.append(
                RecoveryOperation(
                    RecoveryOperationKind.MOVE,
                    transitions[0],
                    transitions[1],
                    operation.case_only,
                )
            )
    return tuple(recovery)


def _recovery_state(state: object) -> RecoveryPathState:
    return RecoveryPathState(
        state.relative_path, state.existed, state.sha256, state.size
    )
