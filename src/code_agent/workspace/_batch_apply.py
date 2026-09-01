from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ._batch_models import (
    BatchApplyResult,
    BatchApplyStatus,
    BatchConflict,
    BatchEditPlan,
    BatchOperation,
    DeletePlan,
    MovePlan,
    PlannedPathState,
)
from ._batch_compare import (
    conflicts_for_mixed as _conflicts_for_mixed,
    inspection_conflicts as _inspection_conflicts,
    observation_matches as _observation_matches,
    observation_tuple_matches as _observation_tuple_matches,
    public_tuple_matches as _public_tuple_matches,
)
from ._batch_mutation import (
    delete_path_exact,
    move_path_exact,
    preflight_move_exact,
    write_bytes_exact,
)
from ._batch_observe import (
    PathObservation,
    alias_state,
    existing_state,
    missing_state,
    observe,
    same_public_state,
)
from ._batch_plan import _plan_id, validate_batch_structure
from ._batch_rollback import AppliedOperation, rollback_operations
from ._edit_plan import EditPlan
from .errors import BatchEditConflictError, CrossVolumeMoveError


@dataclass(frozen=True)
class PreparedBatchEdit:
    plan: BatchEditPlan
    observations: tuple[PathObservation, ...]


def preflight_batch(editor: object, plan: BatchEditPlan) -> PreparedBatchEdit:
    if type(plan) is not BatchEditPlan:
        raise TypeError("plan must be a BatchEditPlan")
    if plan.schema_version != 1:
        raise ValueError("unsupported batch plan schema")
    if plan.workspace_identity != editor.guard.root_identity:
        raise BatchEditConflictError("batch plan belongs to another workspace")
    validate_batch_structure(plan)
    expected_id = _plan_id(plan.workspace_identity, plan.operations, plan.paths)
    if plan.plan_id != expected_id:
        raise ValueError("batch plan identifier does not match its contents")
    observations = tuple(observe(editor, item.relative_path) for item in plan.paths)
    for expected, current in zip(plan.paths, observations):
        if not same_public_state(expected, current.state):
            raise BatchEditConflictError(
                f"batch preflight conflict: {expected.relative_path}"
            )
    observed = {item.state.relative_path: item for item in observations}
    for operation in plan.operations:
        if type(operation) is MovePlan:
            source = observed[operation.source.relative_path]
            destination = observed[operation.destination.relative_path]
            assert source.identity is not None
            preflight_move_exact(
                editor,
                operation.source,
                operation.destination,
                source_identity=source.identity,
                destination_identity=destination.identity,
            )
    return PreparedBatchEdit(plan, observations)


def apply_batch(editor: object, plan: BatchEditPlan) -> BatchApplyResult:
    prepared = preflight_batch(editor, plan)
    expected = {
        item.state.relative_path: item for item in prepared.observations
    }
    initial = dict(expected)
    applied: list[AppliedOperation] = []
    primary: BaseException | None = None
    uncertain_conflicts: list[BatchConflict] = []
    for index, operation in enumerate(plan.operations):
        validation_error, drift = _validation_failure(editor, expected)
        if validation_error is not None:
            primary = validation_error
            uncertain_conflicts.extend(drift)
            break
        paths = _operation_paths(operation)
        before = tuple(initial[path] for path in paths)
        post = _post_states(operation, before)
        try:
            _execute_operation(
                editor,
                operation,
                before,
                lambda: _validate_all(editor, expected),
            )
        except BaseException as error:
            primary = error
            try:
                classification, observations = _classify_current(
                    editor, before, post
                )
            except BaseException as inspection_error:
                uncertain_conflicts.extend(
                    _inspection_conflicts(before, inspection_error)
                )
                break
            if classification == "post":
                applied.append(AppliedOperation(index, operation, before, observations))
            elif classification == "mixed":
                uncertain_conflicts.extend(_conflicts_for_mixed(before, observations))
            break
        try:
            after = tuple(observe(editor, state.relative_path) for state in post)
        except BaseException as inspection_error:
            primary = inspection_error
            uncertain_conflicts.extend(
                _inspection_conflicts(before, inspection_error)
            )
            break
        if not _public_tuple_matches(after, post):
            primary = BatchEditConflictError(
                f"batch postcondition failed at operation {index}"
            )
            uncertain_conflicts.extend(_conflicts_for_mixed(before, after))
            break
        applied.append(AppliedOperation(index, operation, before, after))
        expected.update({item.state.relative_path: item for item in after})
        validation_error, drift = _validation_failure(editor, expected)
        if validation_error is not None:
            primary = validation_error
            uncertain_conflicts.extend(drift)
            break
    if primary is None:
        return BatchApplyResult(
            BatchApplyStatus.APPLIED,
            applied_operations=tuple(item.index for item in applied),
        )
    rolled_back, rollback_conflicts = rollback_operations(editor, applied)
    conflicts = tuple(uncertain_conflicts + rollback_conflicts)
    if isinstance(primary, CrossVolumeMoveError):
        setattr(primary, "rollback_conflicts", conflicts)
        raise primary
    status = (
        BatchApplyStatus.PARTIAL_CONFLICT
        if conflicts
        else BatchApplyStatus.ROLLED_BACK
    )
    return BatchApplyResult(
        status,
        applied_operations=tuple(item.index for item in applied),
        rolled_back_operations=tuple(rolled_back),
        conflicts=conflicts,
        error=f"{type(primary).__name__}: {primary}",
    )


def _execute_operation(
    editor: object,
    operation: BatchOperation,
    before: tuple[PathObservation, ...],
    validate: Callable[[], None],
) -> None:
    if type(operation) is EditPlan:
        assert operation.after_bytes is not None
        write_bytes_exact(
            editor,
            before[0].state,
            operation.after_bytes,
            validate,
            expected_identity=before[0].identity,
        )
    elif type(operation) is DeletePlan:
        assert before[0].identity is not None
        delete_path_exact(
            editor,
            operation.source,
            validate,
            expected_identity=before[0].identity,
        )
    elif type(operation) is MovePlan:
        assert before[0].identity is not None
        move_path_exact(
            editor,
            operation.source,
            operation.destination,
            validate,
            source_identity=before[0].identity,
            destination_identity=before[1].identity,
        )
    else:
        raise TypeError("unsupported batch operation")


def _operation_paths(operation: BatchOperation) -> tuple[str, ...]:
    if type(operation) is EditPlan:
        return (operation.relative_path,)
    if type(operation) is DeletePlan:
        return (operation.source.relative_path,)
    assert type(operation) is MovePlan
    return (operation.source.relative_path, operation.destination.relative_path)


def _post_states(
    operation: BatchOperation,
    before: tuple[PathObservation, ...],
) -> tuple[PlannedPathState, ...]:
    if type(operation) is EditPlan:
        assert operation.after_bytes is not None
        return (existing_state(operation.relative_path, operation.after_bytes),)
    if type(operation) is DeletePlan:
        return (missing_state(operation.source.relative_path),)
    assert type(operation) is MovePlan
    content = before[0].content
    assert content is not None
    source = (
        alias_state(operation.source.relative_path, content)
        if operation.case_only
        else missing_state(operation.source.relative_path)
    )
    return (source, existing_state(operation.destination.relative_path, content))


def _validate_all(
    editor: object, expected: dict[str, PathObservation]
) -> None:
    for wanted in expected.values():
        current = observe(editor, wanted.state.relative_path)
        if not _observation_matches(current, wanted):
            raise BatchEditConflictError(
                f"batch path drifted: {wanted.state.relative_path}"
            )


def _validation_failure(
    editor: object, expected: dict[str, PathObservation]
) -> tuple[BaseException | None, list[BatchConflict]]:
    try:
        _validate_all(editor, expected)
    except BaseException as error:
        conflicts: list[BatchConflict] = []
        for wanted in expected.values():
            try:
                current = observe(editor, wanted.state.relative_path)
            except BaseException as inspection_error:
                conflicts.append(
                    BatchConflict(
                        wanted.state.relative_path,
                        f"cannot verify drifted path: {inspection_error}",
                    )
                )
                continue
            if not _observation_matches(current, wanted):
                conflicts.append(
                    BatchConflict(
                        wanted.state.relative_path, "batch path drifted"
                    )
                )
        return error, conflicts
    return None, []


def _classify_current(
    editor: object,
    before: tuple[PathObservation, ...],
    post: tuple[PlannedPathState, ...],
) -> tuple[str, tuple[PathObservation, ...]]:
    current = tuple(observe(editor, item.state.relative_path) for item in before)
    if _observation_tuple_matches(current, before):
        return "pre", current
    if _public_tuple_matches(current, post):
        return "post", current
    return "mixed", current
