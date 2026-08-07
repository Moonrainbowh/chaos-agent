from __future__ import annotations

from dataclasses import dataclass

from .models import LifecycleEvent


@dataclass(frozen=True)
class TrustedExecutionTrace:
    """Events captured by a harness-owned adapter, never copied from model output."""

    task_status: str | None = None
    policy_events: tuple[str, ...] = ()
    model_turns: int | None = None
    tool_calls: int | None = None
    evidence_generation: int | None = None
    lifecycle_events: tuple[LifecycleEvent, ...] = ()
    sealed: bool = False


class TrustedTraceRecorder:
    """Mutable injection point for production event-bus adapters."""

    def __init__(self) -> None:
        self._status: str | None = None
        self._policy: list[str] = []
        self._turns: int | None = None
        self._tools: int | None = None
        self._generation: int | None = None
        self._lifecycle: list[LifecycleEvent] = []
        self._sealed = False

    def record_status(self, status: str) -> None:
        self._ensure_open()
        if not isinstance(status, str) or not status.strip() or len(status) > 128:
            raise ValueError("trusted status must be non-blank")
        self._status = status

    def record_policy(self, event: str) -> None:
        self._ensure_open()
        if not isinstance(event, str) or not event.strip() or len(event) > 128:
            raise ValueError("trusted policy event must be non-blank")
        if len(self._policy) >= 128:
            raise ValueError("trusted policy trace is too large")
        self._policy.append(event)

    def record_budget(self, *, model_turns: int, tool_calls: int) -> None:
        self._ensure_open()
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (model_turns, tool_calls)
        ):
            raise ValueError("trusted budget counters must be non-negative")
        self._turns = model_turns
        self._tools = tool_calls

    def record_evidence_generation(self, generation: int) -> None:
        self._ensure_open()
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("trusted evidence generation must be non-negative")
        self._generation = generation

    def record_lifecycle(self, event: LifecycleEvent) -> None:
        self._ensure_open()
        if not isinstance(event, LifecycleEvent):
            raise TypeError("trusted lifecycle event must be typed")
        if len(self._lifecycle) >= 128:
            raise ValueError("trusted lifecycle trace is too large")
        self._lifecycle.append(event)

    def seal(self) -> None:
        self._ensure_open()
        self._sealed = True

    def snapshot(self) -> TrustedExecutionTrace:
        return TrustedExecutionTrace(
            self._status,
            tuple(self._policy),
            self._turns,
            self._tools,
            self._generation,
            tuple(self._lifecycle),
            self._sealed,
        )

    def _ensure_open(self) -> None:
        if self._sealed:
            raise RuntimeError("trusted trace is sealed")
