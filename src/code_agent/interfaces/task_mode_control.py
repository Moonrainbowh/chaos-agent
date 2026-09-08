from __future__ import annotations

from dataclasses import dataclass


_MODES = {
    "ask": "Pure Q&A. Workspace writes and local execution are disabled.",
    "code": "Programming mode. Tools remain governed by the permission policy.",
    "plan": "Read-only planning. Workspace writes and local execution are disabled.",
}


@dataclass(frozen=True)
class TaskModeSummary:
    name: str
    description: str


class TaskModeControl:
    """Own the interaction contract applied to newly created tasks."""

    def __init__(self, initial: str = "code") -> None:
        self._current = self._resolve(initial)

    @property
    def current(self) -> TaskModeSummary:
        return self._current

    def list(self) -> tuple[TaskModeSummary, ...]:
        return tuple(TaskModeSummary(name, detail) for name, detail in _MODES.items())

    async def use(self, name: str, *, idle: bool) -> TaskModeSummary:
        if not idle:
            raise RuntimeError("task mode switching is available only when idle")
        self._current = self._resolve(name)
        return self._current

    @staticmethod
    def _resolve(name: str) -> TaskModeSummary:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("task mode must be ask, code, or plan")
        normalized = name.casefold()
        try:
            return TaskModeSummary(normalized, _MODES[normalized])
        except KeyError:
            raise ValueError("task mode must be ask, code, or plan") from None
