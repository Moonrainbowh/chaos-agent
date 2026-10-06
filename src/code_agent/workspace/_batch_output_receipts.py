from __future__ import annotations

from ._batch_models import BatchOperation, DeletePlan, MovePlan, PlannedPathState
from ._batch_observe import PathObservation
from ._secure_io import PathIdentity


def operation_identities(operation: BatchOperation, output: PathIdentity | None) -> tuple[PathIdentity | None, ...]:
    """Bind the actual published object to exact and case-alias endpoints."""
    if type(operation) is DeletePlan:
        return (None,)
    if type(output) is not PathIdentity:
        raise ValueError("missing trusted output identity")
    if type(operation) is MovePlan:
        return (output if operation.case_only else None, output)
    return (output,)


def error_identities(operation: BatchOperation, error: BaseException) -> tuple[PathIdentity | None, ...] | None:
    if not getattr(error, 'publication_committed', False):
        return None
    try:
        return operation_identities(operation, getattr(error, 'output_identity', None))
    except ValueError:
        return None


def trusted_observations(post: tuple[PlannedPathState, ...], current: tuple[PathObservation, ...], identities: tuple[PathIdentity | None, ...]) -> tuple[PathObservation, ...]:
    if len(post) != len(current) or len(post) != len(identities):
        raise ValueError('output receipt endpoint count mismatch')
    return tuple(PathObservation(state, actual.content, identity) for state, actual, identity in zip(post, current, identities))
