from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from enum import Enum


class SteeringStage(str, Enum):
    QUEUED = "queued"
    STEERED = "steered"
    DEQUEUED = "dequeued"
    APPLIED = "applied"


@dataclass(frozen=True)
class SteeringItem:
    identifier: str
    instruction: str
    stage: SteeringStage = SteeringStage.QUEUED

    def __post_init__(self) -> None:
        if not isinstance(self.identifier, str) or not self.identifier.strip():
            raise ValueError("identifier must be non-blank")
        if not isinstance(self.instruction, str) or not self.instruction.strip() or len(self.instruction) > 1_024:
            raise ValueError("instruction must be bounded non-blank text")
        if not isinstance(self.stage, SteeringStage):
            raise TypeError("stage must be SteeringStage")


class SteeringQueueView:
    def __init__(self, *, capacity: int = 100) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._items: list[SteeringItem] = []

    def queue(self, instruction: str, identifier: str | None = None) -> SteeringItem:
        item = SteeringItem(identifier or uuid.uuid4().hex, instruction)
        self._items.append(item)
        if len(self._items) > self._capacity:
            self._items.pop(0)
        return item

    def transition(self, identifier: str, stage: SteeringStage) -> SteeringItem:
        if not isinstance(stage, SteeringStage):
            raise TypeError("stage must be SteeringStage")
        for index, item in enumerate(self._items):
            if item.identifier != identifier:
                continue
            if _ORDER[stage] < _ORDER[item.stage]:
                raise ValueError("steering stage cannot move backward")
            updated = replace(item, stage=stage)
            self._items[index] = updated
            return updated
        raise KeyError("steering item is unknown")

    @property
    def items(self) -> tuple[SteeringItem, ...]:
        return tuple(self._items)

    @property
    def pending_count(self) -> int:
        return sum(item.stage is not SteeringStage.APPLIED for item in self._items)

    def status_line(self) -> str:
        if not self._items:
            return ""
        latest = self._items[-1]
        return f"{latest.stage.value} · queue {self.pending_count}"


_ORDER = {stage: index for index, stage in enumerate(SteeringStage)}
