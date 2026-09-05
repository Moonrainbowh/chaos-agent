from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from enum import Enum


class SteeringStage(str, Enum):
    QUEUED = "queued"
    STEERED = "steered"
    DEQUEUED = "dequeued"
    APPLIED = "applied"


class SteeringKind(str, Enum):
    QUEUE = "queue"
    STEER = "steer"

    @property
    def label(self) -> str:
        return "queue" if self is SteeringKind.QUEUE else "steer"


@dataclass(frozen=True)
class SteeringItem:
    identifier: str
    instruction: str
    kind: SteeringKind = SteeringKind.QUEUE
    stage: SteeringStage = SteeringStage.QUEUED

    def __post_init__(self) -> None:
        if not isinstance(self.identifier, str) or not self.identifier.strip():
            raise ValueError("identifier must be non-blank")
        if not isinstance(self.instruction, str) or not self.instruction.strip() or len(self.instruction) > 1_024:
            raise ValueError("instruction must be bounded non-blank text")
        if not isinstance(self.stage, SteeringStage):
            raise TypeError("stage must be SteeringStage")
        if not isinstance(self.kind, SteeringKind):
            raise TypeError("kind must be SteeringKind")


class SteeringQueueView:
    def __init__(self, *, capacity: int = 100) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._items: list[SteeringItem] = []
        self._early_applied: list[str] = []

    def queue(
        self,
        instruction: str,
        identifier: str | None = None,
        *,
        kind: SteeringKind = SteeringKind.QUEUE,
    ) -> SteeringItem:
        resolved = identifier or uuid.uuid4().hex
        stage = (
            SteeringStage.APPLIED
            if resolved in self._early_applied
            else SteeringStage.QUEUED
        )
        if stage is SteeringStage.APPLIED:
            self._early_applied.remove(resolved)
        item = SteeringItem(resolved, instruction, kind, stage)
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

    def mark_applied(self, identifier: str) -> bool:
        """Apply a promotion, retaining an event that raced ahead of its view item."""
        if not isinstance(identifier, str) or not identifier.strip():
            return False
        try:
            self.transition(identifier, SteeringStage.APPLIED)
        except KeyError:
            if identifier not in self._early_applied:
                self._early_applied.append(identifier)
                if len(self._early_applied) > self._capacity:
                    self._early_applied.pop(0)
            return False
        return True

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
        return f"[{latest.kind.label}] {latest.stage.value} · queue {self.pending_count}"


_ORDER = {stage: index for index, stage in enumerate(SteeringStage)}
