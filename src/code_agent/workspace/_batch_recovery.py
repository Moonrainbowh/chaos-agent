from __future__ import annotations

import hashlib
from pathlib import Path

from ._batch_models import (
    BatchApplyResult,
    BatchApplyStatus,
    BatchConflict,
    PathTransition,
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryPathState,
)
from ._batch_recovery_prepare import (
    recovery_operations_from_prepared,
    snapshot_from_prepared,
)
from ._batch_mutation import delete_path_exact, move_path_exact, write_bytes_exact
from ._batch_observe import PathObservation, observe
from ._batch_recovery_validation import validate_recovery_layout
from ._secure_io import canonical_path_key
from .edits import WorkspaceSnapshot
from .errors import BatchEditConflictError, SnapshotIntegrityError


def recover_batch(
    editor: object,
    operations: tuple[RecoveryOperation, ...],
    snapshot: WorkspaceSnapshot,
) -> BatchApplyResult:
    if type(operations) is not tuple or not all(
        type(item) is RecoveryOperation for item in operations
    ):
        raise TypeError("operations must be RecoveryOperation values")
    if type(snapshot) is not WorkspaceSnapshot:
        raise TypeError("snapshot must be a WorkspaceSnapshot")
    validate_recovery_layout(operations)
    blobs = _snapshot_blobs(snapshot)
    classifications_list: list[str] = []
    classification_errors: dict[int, BaseException] = {}
    for index, operation in enumerate(operations):
        try:
            classifications_list.append(_classify(editor, operation))
        except BaseException as error:
            classifications_list.append("foreign")
            classification_errors[index] = error
    classifications = tuple(classifications_list)
    foreign = [index for index, item in enumerate(classifications) if item == "foreign"]
    if foreign:
        return BatchApplyResult(
            BatchApplyStatus.PARTIAL_CONFLICT,
            conflicts=tuple(
                BatchConflict(
                    operations[index].source.before.relative_path,
                    "recovery state cannot be classified: "
                    f"{classification_errors[index]}"
                    if index in classification_errors
                    else "recovery state is neither PRE nor POST",
                )
                for index in foreign
            ),
        )
    _validate_recovery_snapshot(operations, classifications, blobs)
    rolled_back: list[int] = []
    conflicts: list[BatchConflict] = []
    expected_positions = list(classifications)
    for index in reversed(range(len(operations))):
        if classifications[index] != "post":
            continue
        drift = _position_drift(editor, operations, expected_positions)
        if drift:
            conflicts.extend(drift)
            break
        operation = operations[index]
        try:
            _recover_operation(editor, operation, blobs)
        except BaseException as error:
            conflicts.append(
                BatchConflict(operation.source.before.relative_path, str(error))
            )
            continue
        rolled_back.append(index)
        expected_positions[index] = "pre"
    return BatchApplyResult(
        BatchApplyStatus.PARTIAL_CONFLICT
        if conflicts
        else BatchApplyStatus.ROLLED_BACK,
        applied_operations=tuple(
            index for index, item in enumerate(classifications) if item == "post"
        ),
        rolled_back_operations=tuple(rolled_back),
        conflicts=tuple(conflicts),
    )


def _recover_operation(
    editor: object,
    operation: RecoveryOperation,
    blobs: dict[str, bytes | None],
) -> None:
    transitions = _transitions(operation)

    def validate() -> None:
        if any(item != "post" for item in _positions(editor, operation)):
            raise BatchEditConflictError("recovery POST state drifted")

    validate()
    current = tuple(observe(editor, item.after.relative_path) for item in transitions)
    kind = operation.kind
    if kind in (RecoveryOperationKind.CREATE, RecoveryOperationKind.UPDATE):
        if kind is RecoveryOperationKind.CREATE:
            identity = current[0].identity
            assert identity is not None
            delete_path_exact(
                editor,
                current[0].state,
                validate,
                expected_identity=identity,
            )
        else:
            write_bytes_exact(
                editor,
                current[0].state,
                _preimage(blobs, operation.source.before),
                validate,
                expected_identity=current[0].identity,
            )
    elif kind is RecoveryOperationKind.DELETE:
        write_bytes_exact(
            editor,
            current[0].state,
            _preimage(blobs, operation.source.before),
            validate,
            expected_identity=current[0].identity,
        )
    else:
        assert kind is RecoveryOperationKind.MOVE and len(current) == 2
        source_identity = current[1].identity
        assert source_identity is not None
        move_path_exact(
            editor,
            current[1].state,
            current[0].state,
            validate,
            source_identity=source_identity,
            destination_identity=current[0].identity,
        )
    if any(item != "pre" for item in _positions(editor, operation)):
        raise BatchEditConflictError("recovery PRE state was not restored")


def _classify(editor: object, operation: RecoveryOperation) -> str:
    positions = _positions(editor, operation)
    if all(item == "pre" for item in positions):
        return "pre"
    if all(item == "post" for item in positions):
        return "post"
    return "foreign"


def _position_drift(
    editor: object,
    operations: tuple[RecoveryOperation, ...],
    expected: list[str],
) -> list[BatchConflict]:
    conflicts: list[BatchConflict] = []
    for index, operation in enumerate(operations):
        try:
            changed = _classify(editor, operation) != expected[index]
            reason = "recovery state drifted after classification"
        except BaseException as error:
            changed = True
            reason = f"cannot classify recovery state: {error}"
        if changed:
            conflicts.append(
                BatchConflict(
                    operation.source.before.relative_path,
                    reason,
                )
            )
    return conflicts


def _path_position(
    editor: object,
    transition: PathTransition,
    *,
    allow_lookup_alias: bool = False,
) -> str:
    current = observe(editor, transition.before.relative_path).state
    exact = RecoveryPathState(
        current.relative_path, current.existed, current.sha256, current.size
    )
    if not current.existed and current.lookup_existed and not allow_lookup_alias:
        return "foreign"
    if exact == transition.before:
        return "pre"
    if exact == transition.after:
        return "post"
    return "foreign"


def _positions(editor: object, operation: RecoveryOperation) -> tuple[str, ...]:
    return tuple(
        _path_position(
            editor,
            item,
            allow_lookup_alias=operation.case_only,
        )
        for item in _transitions(operation)
    )


def _transitions(operation: RecoveryOperation) -> tuple[PathTransition, ...]:
    if operation.destination is None:
        return (operation.source,)
    return operation.source, operation.destination


def _snapshot_blobs(snapshot: WorkspaceSnapshot) -> dict[str, bytes | None]:
    blobs: dict[str, bytes | None] = {}
    for entry in snapshot.entries:
        key = canonical_path_key(entry.relative_path)
        if key in blobs:
            raise SnapshotIntegrityError("recovery snapshot contains duplicate paths")
        blobs[key] = entry.content if entry.existed else None
    return blobs


def _preimage(
    blobs: dict[str, bytes | None], state: RecoveryPathState
) -> bytes:
    content = blobs.get(canonical_path_key(state.relative_path))
    if content is None:
        raise SnapshotIntegrityError(f"recovery preimage is missing: {state.relative_path}")
    if len(content) != state.size or hashlib.sha256(content).hexdigest() != state.sha256:
        raise SnapshotIntegrityError(f"recovery preimage is corrupt: {state.relative_path}")
    return content


def _validate_recovery_snapshot(
    operations: tuple[RecoveryOperation, ...],
    classifications: tuple[str, ...],
    blobs: dict[str, bytes | None],
) -> None:
    for operation, position in zip(operations, classifications):
        if position != "post":
            continue
        if operation.kind in (
            RecoveryOperationKind.UPDATE,
            RecoveryOperationKind.DELETE,
            RecoveryOperationKind.MOVE,
        ):
            _preimage(blobs, operation.source.before)
        source_key = canonical_path_key(operation.source.before.relative_path)
        if source_key not in blobs:
            raise SnapshotIntegrityError("recovery snapshot is missing a source path")
        if operation.destination is not None and not operation.case_only:
            destination_key = canonical_path_key(
                operation.destination.before.relative_path
            )
            if destination_key not in blobs:
                raise SnapshotIntegrityError(
                    "recovery snapshot is missing a destination path"
                )
