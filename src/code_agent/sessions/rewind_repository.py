from __future__ import annotations

from ._edit_batches import EditBatchRepositoryMixin
from ._rewind_checkpoints import RewindCheckpointRepositoryMixin
from ._rewind_mutations import RewindMutationRepositoryMixin
from ._rewind_observations import RewindObservationRepositoryMixin
from .repository import SQLiteSessionRepository


class RewindSessionRepository(
    EditBatchRepositoryMixin,
    RewindCheckpointRepositoryMixin,
    RewindObservationRepositoryMixin,
    RewindMutationRepositoryMixin,
    SQLiteSessionRepository,
):
    """Persist legacy sessions plus trusted rewind facts."""
