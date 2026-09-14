from __future__ import annotations

from typing import Mapping

from ._batch_apply import PreparedBatchEdit, _operation_paths, _post_states
from ._batch_observe import observe
from ._batch_models import (
    DeletePlan,
    MovePlan,
    PathTransition,
    PlannedPathState,
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryPathState,
)
from ._edit_plan import EditPlan
from ._secure_io import PathIdentity, canonical_path_key
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


def post_identities(
    editor: object, prepared: PreparedBatchEdit
) -> dict[str, PathIdentity | None]:
    """Observe the durable identity of every path a prepared batch touches.

    This is the only moment the POST identity exists. An atomic replace installs
    a new file index, and a created file does not exist before the write, so the
    value cannot be derived at plan time. The caller persists the result as the
    ownership proof recovery needs after a crash.
    """
    if type(prepared) is not PreparedBatchEdit:
        raise TypeError("prepared must be a PreparedBatchEdit")
    identities: dict[str, PathIdentity | None] = {}
    for operation in prepared.plan.operations:
        for relative in _operation_paths(operation):
            if relative not in identities:
                identities[relative] = observe(editor, relative).identity
    return identities


def recovery_operations_from_prepared(
    prepared: PreparedBatchEdit,
    post_identities: Mapping[str, PathIdentity | None] | None = None,
) -> tuple[RecoveryOperation, ...]:
    """Build durable recovery transitions for a prepared batch.

    PRE identities come from the preflight observations. POST identities cannot
    be known when the journal is written: an atomic replace yields a new file
    index, and a created file does not exist yet. They are therefore supplied
    separately through ``post_identities`` once the batch has actually been
    applied. A POST path left without an identity stays unprovable, and
    recovery refuses to classify it rather than trusting content alone.
    """
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
        post_ids = [
            _recorded_post_identity(post_identities, state) for state in after
        ]
        if type(operation) is MovePlan and not operation.case_only:
            # A move relocates one file object, so the destination POST state
            # carries the source PRE identity even before it is observed.
            post_ids[1] = post_ids[1] or before[0].identity
        transitions = tuple(
            PathTransition(
                _recovery_state(old.state, old.identity),
                _recovery_state(new, new_identity),
            )
            for old, new, new_identity in zip(before, after, post_ids)
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


def _recorded_post_identity(
    post_identities: Mapping[str, PathIdentity | None] | None,
    state: PlannedPathState,
) -> PathIdentity | None:
    if not state.existed or post_identities is None:
        return None
    return post_identities.get(state.relative_path)


def _recovery_state(
    state: object, identity: PathIdentity | None = None
) -> RecoveryPathState:
    if not state.existed:
        identity = None
    return RecoveryPathState(
        state.relative_path, state.existed, state.sha256, state.size, identity
    )
