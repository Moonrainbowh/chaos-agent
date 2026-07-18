from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from code_agent.orchestration.models import AgentMode, ModeSnapshot


@dataclass(frozen=True)
class ModeSummary:
    name: str
    model: str
    reasoning_effort: str


class ModeControl:
    """Select a pre-frozen Agent mode only at an idle task boundary."""

    def __init__(
        self,
        snapshots: Mapping[AgentMode, ModeSnapshot],
        current: AgentMode,
        apply: Callable[[ModeSnapshot], Awaitable[None]],
    ) -> None:
        self._snapshots = dict(snapshots)
        self._current = AgentMode(current)
        self._apply = apply
        if set(self._snapshots) != set(AgentMode):
            raise ValueError("all standard agent modes must be configured")
        if self._current not in self._snapshots:
            raise ValueError("current agent mode is not configured")

    @property
    def current(self) -> ModeSummary:
        return _summary(self._snapshots[self._current])

    def list(self) -> tuple[ModeSummary, ...]:
        return tuple(_summary(self._snapshots[mode]) for mode in AgentMode)

    async def use(self, name: str, *, idle: bool) -> ModeSummary:
        if not idle:
            raise RuntimeError("mode switching is available only when idle")
        try:
            selected = AgentMode(name)
        except ValueError:
            raise ValueError("unknown agent mode") from None
        snapshot = self._snapshots[selected]
        await self._apply(snapshot)
        self._current = selected
        return _summary(snapshot)


def _summary(snapshot: ModeSnapshot) -> ModeSummary:
    return ModeSummary(
        snapshot.definition.mode.value,
        snapshot.model,
        snapshot.definition.reasoning_effort.value,
    )
