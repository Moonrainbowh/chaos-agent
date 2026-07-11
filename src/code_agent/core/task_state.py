from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskState:
    """Immutable task-state placeholder until persistence is introduced."""

    @classmethod
    def empty(cls) -> TaskState:
        return cls()
