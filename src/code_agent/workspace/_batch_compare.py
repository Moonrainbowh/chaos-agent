from __future__ import annotations

from ._batch_models import BatchConflict, PlannedPathState
from ._batch_observe import PathObservation, same_public_state
from ._secure_io import same_path_state


def observation_matches(
    current: PathObservation, expected: PathObservation
) -> bool:
    return same_public_state(current.state, expected.state) and same_path_state(
        current.identity, expected.identity
    )


def observation_tuple_matches(
    current: tuple[PathObservation, ...],
    expected: tuple[PathObservation, ...],
) -> bool:
    return len(current) == len(expected) and all(
        observation_matches(actual, wanted)
        for actual, wanted in zip(current, expected)
    )


def public_tuple_matches(
    current: tuple[PathObservation, ...],
    expected: tuple[PlannedPathState, ...],
) -> bool:
    return len(current) == len(expected) and all(
        same_public_state(item.state, state)
        for item, state in zip(current, expected)
    )


def conflicts_for_mixed(
    before: tuple[PathObservation, ...],
    current: tuple[PathObservation, ...],
) -> list[BatchConflict]:
    return [
        BatchConflict(old.state.relative_path, "operation ended in an uncertain state")
        for old, now in zip(before, current)
        if not observation_matches(now, old)
    ]


def inspection_conflicts(
    paths: tuple[PathObservation, ...], error: BaseException
) -> list[BatchConflict]:
    return [
        BatchConflict(
            item.state.relative_path,
            f"cannot classify operation state: {error}",
        )
        for item in paths
    ]
