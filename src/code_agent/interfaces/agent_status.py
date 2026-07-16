from __future__ import annotations

import time
from collections.abc import Callable

from code_agent.orchestration.models import RunStatus, RunView


class AgentRunStatusProjection:
    """Render each truthful child-run transition once for the transcript."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._statuses: dict[str, RunStatus] = {}
        self._started: dict[str, float] = {}

    def observe(self, view: RunView) -> str | None:
        if self._statuses.get(view.run_id) is view.status:
            return None
        now = self._clock()
        self._statuses[view.run_id] = view.status
        self._started.setdefault(view.run_id, now)
        objective = " ".join(view.objective.split())[:80]
        line = f"agent {view.role.value} · {view.status.value} · {objective}"
        if view.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            elapsed = max(0, int(now - self._started.pop(view.run_id, now)))
            line += (
                f" · {elapsed}s · {view.usage.total_tokens} tokens"
                f" · {view.usage.tool_calls} tools"
            )
        return line
