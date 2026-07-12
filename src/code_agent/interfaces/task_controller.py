from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.task import TaskAuthorization, TaskContract, TaskRecord, TaskStatus
from code_agent.core.models import Message

from .controller import AgentController


class ForegroundTaskController:
    """Own foreground cancellation and task lifecycle, leaving execution to AgentEngine."""

    def __init__(self, controller: AgentController, sessions: object, workspace_root: Path | str) -> None:
        self._controller = controller
        self._sessions = sessions
        self._root = str(Path(workspace_root).resolve())
        self._tokens: dict[str, CancellationToken] = {}

    async def start(self, prompt: str) -> TaskRecord:
        thread_id = await self._sessions.create_thread()
        task = await self._sessions.create_task(thread_id, TaskContract(prompt, TaskAuthorization.local_workspace(self._root)))
        await self._sessions.create_checkpoint(thread_id, "task-created", {"task_id": task.id, "status": task.status.value})
        return task

    async def list(self, *, include_terminal: bool = False) -> tuple[TaskRecord, ...]:
        return await self._sessions.list_tasks(include_terminal=include_terminal)

    async def events(self, task_id: str, prompt: str | None = None) -> AsyncIterator[AgentEvent]:
        task = await self._sessions.load_task(task_id)
        if task.status is not TaskStatus.RUNNING:
            task = await self._sessions.transition_task(task.id, TaskStatus.RUNNING)
        started = AgentEvent(EventKind.TASK_STATUS_CHANGED, {"task_id": task.id, "status": task.status.value})
        await self._sessions.append_event(task.thread_id, started)
        yield started
        token = CancellationToken()
        self._tokens[task.id] = token
        instruction = prompt or task.contract.objective
        try:
            async for event in self._controller.ask(instruction, thread_id=task.thread_id, cancellation=token, task=task):
                yield event
            current = await self._sessions.load_task(task.id)
            if current.status is TaskStatus.RUNNING:
                completed = await self._sessions.transition_task(task.id, TaskStatus.COMPLETED)
                yield AgentEvent(EventKind.TASK_STATUS_CHANGED, {"task_id": completed.id, "status": completed.status.value})
        except Exception:
            current = await self._sessions.load_task(task.id)
            if current.status is TaskStatus.RUNNING:
                await self._sessions.transition_task(task.id, TaskStatus.FAILED, "task execution failed")
            raise
        finally:
            self._tokens.pop(task.id, None)

    async def pause(self, task_id: str, reason: str = "user requested pause") -> None:
        token = self._tokens.get(task_id)
        if token is not None:
            token.cancel(reason)
        task = await self._sessions.transition_task(task_id, TaskStatus.PAUSED, reason)
        await self._sessions.create_checkpoint(
            task.thread_id,
            "task-paused",
            {"task_id": task.id, "status": task.status.value, "reason": reason},
        )

    async def stop(self, task_id: str) -> None:
        token = self._tokens.get(task_id)
        if token is not None:
            token.cancel("user stopped task")
        await self._sessions.transition_task(task_id, TaskStatus.FAILED, "user stopped task")

    async def resume(self, task_id: str, instruction: str = "continue safely") -> AsyncIterator[AgentEvent]:
        async for event in self.events(task_id, instruction):
            yield event

    async def steer(self, task_id: str, instruction: str) -> None:
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 1024:
            raise ValueError("instruction must be bounded non-blank text")
        task = await self._sessions.load_task(task_id)
        await self._sessions.append_message(task.thread_id, Message(role="user", content=instruction))
        await self._sessions.record_task_control(task_id, instruction)
