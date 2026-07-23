from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Mapping

from code_agent.core.task import TaskStatus


@dataclass
class _RunBarrier:
    serial: int
    owner: asyncio.Task[object] | None
    settled: asyncio.Event
    finalized: asyncio.Event


class ForegroundCheckpointLifecycle:
    """Join task execution before publishing durable lifecycle boundaries."""

    _ACTIVE = {TaskStatus.CREATED, TaskStatus.RUNNING, TaskStatus.VERIFYING}
    _SETTLED_LABELS = {
        TaskStatus.PAUSED: "task-paused",
        TaskStatus.INTERRUPTED: "task-interrupted",
        TaskStatus.COMPLETED: "task-completed",
        TaskStatus.ACCEPTED_PARTIAL: "task-accepted-partial",
        TaskStatus.FAILED: "task-failed",
    }

    def __init__(
        self,
        owner: object,
        sessions: object,
        checkpoints: object | None,
        workspace_runtime: object | None,
    ) -> None:
        self._owner, self._sessions = owner, sessions
        self._checkpoints, self._workspace_runtime = checkpoints, workspace_runtime
        self._runs: dict[str, _RunBarrier] = {}
        self._serials: dict[str, int] = {}
        self._quiescing: set[str] = set()
        self._capture_locks: dict[str, asyncio.Lock] = {}
        self._quiesce_locks: dict[str, asyncio.Lock] = {}
        self._capture_owners: dict[str, asyncio.Task[object] | None] = {}
        self._captured: set[tuple[str, int, str]] = set()

    def begin_run(self, task_id: str) -> int:
        current = self._runs.get(task_id)
        if current is not None and not current.finalized.is_set():
            raise RuntimeError("task execution is active or finalizing")
        if task_id in self._quiescing:
            raise RuntimeError("task workspace is quiescing")
        serial = self._serials.get(task_id, 0) + 1
        self._serials[task_id] = serial
        self._runs[task_id] = _RunBarrier(
            serial,
            asyncio.current_task(),
            asyncio.Event(),
            asyncio.Event(),
        )
        return serial

    def settle_run(self, task_id: str, serial: int) -> None:
        barrier = self._runs.get(task_id)
        if barrier is None or barrier.serial != serial:
            return
        barrier.settled.set()

    def finalize_run(self, task_id: str, serial: int) -> None:
        barrier = self._runs.get(task_id)
        if barrier is None or barrier.serial != serial:
            return
        barrier.finalized.set()
        self._runs.pop(task_id, None)

    def serial(self, task_id: str) -> int:
        return self._serials.get(task_id, 0)

    async def quiesce(self, task_id: str, reason: str = "checkpoint quiesce"):
        return await self._quiesce_to(
            task_id, reason, TaskStatus.PAUSED, self._ACTIVE
        )

    async def pause(self, task_id: str, reason: str) -> None:
        task = await self._quiesce_to(
            task_id, reason, TaskStatus.PAUSED, self._ACTIVE
        )
        if task.status is TaskStatus.PAUSED:
            await self._capture_boundary(task, "task-paused", reason)

    async def interrupt(self, task_id: str, reason: str) -> None:
        task = await self._sessions.load_task(task_id)
        if task.status not in {TaskStatus.RUNNING, TaskStatus.VERIFYING}:
            return
        task = await self._quiesce_to(
            task_id,
            reason,
            TaskStatus.INTERRUPTED,
            {TaskStatus.RUNNING, TaskStatus.VERIFYING},
        )
        if task.status is TaskStatus.INTERRUPTED:
            await self._capture_boundary(task, "task-interrupted", reason)

    async def accept_partial(self, task_id: str, reason: str):
        task = await self._sessions.load_task(task_id)
        allowed = {TaskStatus.VERIFYING, TaskStatus.WAITING_DECISION}
        if task.status not in allowed:
            raise RuntimeError(
                "only verifying or waiting tasks can be accepted partially"
            )
        task = await self._quiesce_to(
            task_id, reason, TaskStatus.ACCEPTED_PARTIAL, allowed
        )
        if task.status is not TaskStatus.ACCEPTED_PARTIAL:
            raise RuntimeError("task settled before partial acceptance")
        await self._capture_boundary(task, "task-accepted-partial", reason)
        return task

    async def capture(
        self,
        task_id: str,
        label: str,
        metadata: Mapping[str, object],
        serial: int,
    ):
        key = (task_id, serial, label)
        lock = self._capture_locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            if key in self._captured:
                return None
            if await self._durable_available(task_id):
                owner = asyncio.current_task()
                self._capture_owners[task_id] = owner
                try:
                    result = await self._checkpoints.create(task_id, label)
                finally:
                    if self._capture_owners.get(task_id) is owner:
                        self._capture_owners.pop(task_id, None)
            else:
                task = await self._sessions.load_task(task_id)
                result = await self._sessions.create_checkpoint(
                    task.thread_id, label, dict(metadata)
                )
            self._captured.add(key)
            return result

    async def capture_settled(self, task_id: str, serial: int) -> None:
        while task_id in self._quiescing:
            await asyncio.sleep(0.01)
        task = await self._sessions.load_task(task_id)
        label = self._SETTLED_LABELS.get(task.status)
        if label is None:
            return
        await self._capture_boundary(task, label, task.stop_reason, serial)

    async def _quiesce_to(
        self,
        task_id: str,
        reason: str,
        target: TaskStatus,
        allowed: set[TaskStatus],
    ):
        if self._capture_owners.get(task_id) is asyncio.current_task():
            return await self._sessions.load_task(task_id)
        lock = self._quiesce_locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            return await self._quiesce_locked(
                task_id, reason, target, allowed
            )

    async def _quiesce_locked(
        self,
        task_id: str,
        reason: str,
        target: TaskStatus,
        allowed: set[TaskStatus],
    ):
        barrier = self._runs.get(task_id)
        if barrier is not None and barrier.owner is asyncio.current_task():
            raise RuntimeError("cannot quiesce from the active task event stream")
        self._quiescing.add(task_id)
        try:
            while barrier is not None and not barrier.settled.is_set():
                token = self._owner._tokens.get(task_id)
                if token is not None:
                    token.cancel(reason)
                await asyncio.sleep(0.01)
            task = await self._sessions.load_task(task_id)
            if task.status in allowed:
                task = await self._sessions.transition_task(
                    task_id, target, reason
                )
        finally:
            self._quiescing.discard(task_id)
        if barrier is not None:
            await barrier.finalized.wait()
        return task

    async def _capture_boundary(
        self,
        task: object,
        label: str,
        reason: str | None,
        serial: int | None = None,
    ) -> None:
        metadata: dict[str, object] = {
            "task_id": task.id,
            "status": task.status.value,
        }
        if reason:
            metadata["reason"] = reason
        await self.capture(
            task.id,
            label,
            metadata,
            self.serial(task.id) if serial is None else serial,
        )

    async def _durable_available(self, task_id: str) -> bool:
        if self._checkpoints is None:
            return False
        available = getattr(
            self._workspace_runtime, "checkpoint_available", None
        )
        if callable(available):
            return bool(await available(task_id))
        return True
