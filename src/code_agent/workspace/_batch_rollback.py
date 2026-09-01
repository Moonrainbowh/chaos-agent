from __future__ import annotations

from dataclasses import dataclass

from ._batch_models import BatchConflict, BatchOperation, DeletePlan, MovePlan
from ._batch_mutation import delete_path_exact, move_path_exact, write_bytes_exact
from ._batch_observe import PathObservation, observe, same_public_state
from ._edit_plan import EditPlan
from .errors import BatchEditConflictError
from ._secure_io import same_path_state


@dataclass(frozen=True)
class AppliedOperation:
    index: int
    operation: BatchOperation
    before: tuple[PathObservation, ...]
    after: tuple[PathObservation, ...]


def rollback_operations(
    editor: object,
    applied: list[AppliedOperation],
) -> tuple[list[int], list[BatchConflict]]:
    rolled_back: list[int] = []
    conflicts: list[BatchConflict] = []
    for record in reversed(applied):
        try:
            current = tuple(
                observe(editor, item.state.relative_path) for item in record.after
            )
        except BaseException as error:
            conflicts.extend(
                BatchConflict(
                    item.state.relative_path,
                    f"cannot verify owned postimage: {error}",
                )
                for item in record.after
            )
            continue
        mismatched = owned_mismatches(current, record.after)
        if mismatched:
            conflicts.extend(mismatched)
            continue
        try:
            _rollback_operation(editor, record)
            restored = tuple(
                observe(editor, item.state.relative_path) for item in record.before
            )
            if not _tuple_matches(restored, record.before):
                raise BatchEditConflictError("rollback postcondition failed")
        except BaseException as error:
            conflicts.extend(
                BatchConflict(item.state.relative_path, str(error))
                for item in record.after
            )
            continue
        rolled_back.append(record.index)
    return rolled_back, conflicts


def _rollback_operation(editor: object, record: AppliedOperation) -> None:
    operation = record.operation

    def validate() -> None:
        mismatched = owned_mismatches(
            tuple(observe(editor, item.state.relative_path) for item in record.after),
            record.after,
        )
        if mismatched:
            raise BatchEditConflictError(mismatched[0].reason)

    if type(operation) is EditPlan:
        if operation.existed:
            content = record.before[0].content
            assert content is not None
            write_bytes_exact(
                editor,
                record.after[0].state,
                content,
                validate,
                expected_identity=record.after[0].identity,
            )
        else:
            identity = record.after[0].identity
            assert identity is not None
            delete_path_exact(
                editor,
                record.after[0].state,
                validate,
                expected_identity=identity,
            )
        return
    if type(operation) is DeletePlan:
        content = record.before[0].content
        assert content is not None
        write_bytes_exact(
            editor,
            record.after[0].state,
            content,
            validate,
            expected_identity=record.after[0].identity,
        )
        return
    assert type(operation) is MovePlan
    source_identity = record.after[1].identity
    assert source_identity is not None
    move_path_exact(
        editor,
        record.after[1].state,
        record.after[0].state,
        validate,
        source_identity=source_identity,
        destination_identity=record.after[0].identity,
    )


def owned_mismatches(
    current: tuple[PathObservation, ...],
    expected: tuple[PathObservation, ...],
) -> list[BatchConflict]:
    conflicts: list[BatchConflict] = []
    for actual, owned in zip(current, expected):
        same = same_public_state(actual.state, owned.state)
        if owned.identity is not None:
            same = same and same_path_state(actual.identity, owned.identity)
        if not same:
            conflicts.append(
                BatchConflict(owned.state.relative_path, "owned postimage drifted")
            )
    return conflicts


def _tuple_matches(
    current: tuple[PathObservation, ...],
    expected: tuple[PathObservation, ...],
) -> bool:
    return len(current) == len(expected) and all(
        same_public_state(actual.state, wanted.state)
        for actual, wanted in zip(current, expected)
    )
