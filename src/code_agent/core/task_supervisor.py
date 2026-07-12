from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from .task import TaskContract


class SupervisionKind(str, Enum):
    CONTINUE = "continue"
    WARN = "warn"
    PAUSE = "pause"


@dataclass(frozen=True)
class SupervisionDecision:
    kind: SupervisionKind
    reason: str | None = None


class TaskSupervisor:
    """Pure budget and deterministic validation-stall guard for a task."""

    def __init__(self, contract: TaskContract, *, started_at: datetime | None = None) -> None:
        if not isinstance(contract, TaskContract):
            raise TypeError("contract must be a TaskContract")
        self._contract = contract
        self._started_at = started_at or datetime.now(timezone.utc)
        self._last_failure: str | None = None
        self._repetitions = 0
        self._repair_cycles = 0

    def before_model_turn(self) -> SupervisionDecision:
        elapsed = (datetime.now(timezone.utc) - self._started_at).total_seconds()
        if elapsed >= self._contract.max_active_seconds:
            return SupervisionDecision(SupervisionKind.PAUSE, "active time budget exceeded")
        return SupervisionDecision(SupervisionKind.CONTINUE)

    def before_external_action(self) -> SupervisionDecision:
        return self.before_model_turn()

    def observe_validation(self, fingerprint: str | None, changed_files: int) -> SupervisionDecision:
        if changed_files < 0:
            raise ValueError("changed_files must not be negative")
        if not fingerprint or changed_files == 0:
            return SupervisionDecision(SupervisionKind.CONTINUE)
        if fingerprint == self._last_failure:
            self._repetitions += 1
        else:
            self._last_failure = fingerprint
            self._repetitions = 1
        self._repair_cycles += 1
        if self._repetitions >= self._contract.max_repeated_failure_signatures:
            return SupervisionDecision(SupervisionKind.PAUSE, "repeated validation failure")
        if self._repair_cycles >= self._contract.max_repair_cycles:
            return SupervisionDecision(SupervisionKind.PAUSE, "repair cycle budget exceeded")
        return SupervisionDecision(SupervisionKind.CONTINUE)
