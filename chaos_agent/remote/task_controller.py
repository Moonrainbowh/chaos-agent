from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .protocol import MobileEvent, event_from_agent


@dataclass
class _RemoteRun:
    task_id: str
    session_id: str
    events: deque[MobileEvent] = field(default_factory=lambda: deque(maxlen=256))
    sequence: int = 0
    status: str = "created"
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    runner: asyncio.Task[None] | None = None


class RemoteTaskController:
    """Adapt one foreground Agent task to reconnectable mobile events."""

    def __init__(self, application: object) -> None:
        self._application = application
        self._active: _RemoteRun | None = None
        self._lock = asyncio.Lock()

    async def start(self, prompt: str) -> dict[str, str]:
        async with self._lock:
            if self._active is not None and self._active.status in {"created", "running"}:
                raise RuntimeError("a remote task is already active")
            task = await self._application.foreground_tasks.start(prompt)
            run = _RemoteRun(task.id, task.thread_id)
            self._active = run
            await self._publish(run, MobileEvent("task_started", task_id=task.id, session_id=task.thread_id))
            run.runner = asyncio.create_task(self._consume(run), name=f"remote-task-{task.id}")
            return {"task_id": task.id, "session_id": task.thread_id}

    async def stop(self, task_id: str) -> None:
        run = self._require(task_id)
        if run.status not in {"created", "running"}:
            raise RuntimeError("task is no longer active")
        await self._application.foreground_tasks.interrupt(task_id, "remote user stopped task")
        run.status = "stopped"
        await self._publish(run, MobileEvent("task_stopped", task_id=run.task_id, session_id=run.session_id))

    async def status(self) -> dict[str, Any]:
        run = self._active
        if run is None:
            return {"status": "idle", "task_id": None, "session_id": None, "sequence": 0}
        return {"status": run.status, "task_id": run.task_id, "session_id": run.session_id, "sequence": run.sequence}

    async def events(self, since: int = 0) -> AsyncIterator[MobileEvent]:
        run = self._active
        if run is None:
            return
        next_sequence = max(1, since + 1)
        while True:
            async with run.condition:
                pending = [event for event in run.events if event.sequence >= next_sequence]
                if not pending and run.status not in {"created", "running"}:
                    return
                if not pending:
                    await run.condition.wait()
                    continue
            for event in pending:
                next_sequence = event.sequence + 1
                yield event

    async def _consume(self, run: _RemoteRun) -> None:
        run.status = "running"
        try:
            async for event in self._application.foreground_tasks.events(run.task_id):
                mobile = event_from_agent(event, task_id=run.task_id, session_id=run.session_id, sequence=run.sequence + 1)
                if mobile is not None:
                    await self._publish(run, mobile)
                    if mobile.event == "task_status":
                        run.status = str((mobile.data or {}).get("status", run.status))
                    elif mobile.event in {"task_completed", "task_failed", "task_stopped"}:
                        run.status = mobile.event.removeprefix("task_")
        except asyncio.CancelledError:
            raise
        except Exception:
            run.status = "failed"
            await self._publish(run, MobileEvent("task_failed", task_id=run.task_id, session_id=run.session_id, data={"error": "task execution failed"}))
        finally:
            async with run.condition:
                run.condition.notify_all()

    async def _publish(self, run: _RemoteRun, event: MobileEvent) -> None:
        if event.sequence <= run.sequence:
            event = MobileEvent(event.event, event.task_id, event.session_id, run.sequence + 1, event.data)
        run.sequence = event.sequence
        run.events.append(event)
        async with run.condition:
            run.condition.notify_all()

    def _require(self, task_id: str) -> _RemoteRun:
        run = self._active
        if run is None or run.task_id != task_id:
            raise KeyError("unknown task")
        return run
