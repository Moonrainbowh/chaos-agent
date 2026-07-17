from __future__ import annotations

from ._rewind_mutations import RewindMutationRepositoryMixin
from .repository import SQLiteSessionRepository


class RewindSessionRepository(
    RewindMutationRepositoryMixin,
    SQLiteSessionRepository,
):
    """Persist legacy sessions plus trusted rewind facts."""
