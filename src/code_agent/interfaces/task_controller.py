from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path

import psutil

from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.attachments import AttachmentRef, freeze_attachments
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.task import TaskAuthorization, TaskContract, TaskRecord, TaskStatus
from code_agent.core.models import Message

from .controller import AgentController


class ForegroundTaskController:
    """Own foreground cancellation and task lifecycle, leaving execution to AgentEngine."""

    def __init__(self, controller: AgentController, sessions: object, workspace_root: Path | str, *, profile_supplier: Callable[[], tuple[str, str, str, str]] | None = None, profile_resolver: Callable[[str], Awaitable[None]] | None = None) -> None:
        self._controller = controller
        self._sessions = sessions
        self._root = str(Path(workspace_root).resolve())
        self._tokens: dict[str, CancellationToken] = {}
        self._profile_supplier, self._profile_resolver = profile_supplier, profile_resolver

    async def start(self, prompt: str) -> TaskRecord:
        active = await self._sessions.list_tasks()
        if any(
            task.status in {TaskStatus.CREATED, TaskStatus.RUNNING}
            and os.path.normcase(task.contract.authorization.workspace_root) == os.path.normcase(self._root)
            for task in active
        ):
            raise RuntimeError("a foreground task is already active")
        thread_id = await self._sessions.create_thread()
        profile = self._profile_supplier() if self._profile_supplier else None
        task = await self._sessions.create_task(thread_id, TaskContract(prompt, TaskAuthorization.local_workspace(self._root), profile_id=profile[0] if profile else None, model=profile[1] if profile else None, protocol=profile[2] if profile else None, endpoint_host=profile[3] if profile else None))
        await self._sessions.create_checkpoint(thread_id, "task-created", {"task_id": task.id, "status": task.status.value})
        return task

    async def list(self, *, include_terminal: bool = False) -> tuple[TaskRecord, ...]:
        return await self._sessions.list_tasks(include_terminal=include_terminal)

    async def events(
        self,
        task_id: str,
        prompt: str | None = None,
        *,
        attachments: Sequence[AttachmentRef] = (),
    ) -> AsyncIterator[AgentEvent]:
        task = await self._sessions.load_task(task_id)
        if task.contract.profile_id and self._profile_resolver:
            try: await self._profile_resolver(task.contract.profile_id)
            except (RuntimeError, ValueError):
                waiting = await self._sessions.transition_task(task.id, TaskStatus.WAITING_DECISION, "recorded model profile is unavailable")
                event = AgentEvent(EventKind.TASK_DECISION_REQUIRED, {"task_id": waiting.id, "status": waiting.status.value, "reason": "recorded model profile is unavailable"})
                await self._sessions.append_event(waiting.thread_id, event); yield event; return
        if task.status is TaskStatus.INTERRUPTED:
            interrupt_runs = getattr(self._sessions, "interrupt_open_verification_runs", None)
            if callable(interrupt_runs):
                await interrupt_runs(task.id)
        if task.status is not TaskStatus.RUNNING:
            task = await self._sessions.transition_task(task.id, TaskStatus.RUNNING)
        register = getattr(self._sessions, "register_task_execution", None)
        if callable(register):
            process = psutil.Process(os.getpid())
            await register(task.id, uuid.uuid4().hex, process.pid, process.create_time())
        started = AgentEvent(EventKind.TASK_STATUS_CHANGED, {"task_id": task.id, "status": task.status.value})
        await self._sessions.append_event(task.thread_id, started)
        yield started
        token = CancellationToken()
        self._tokens[task.id] = token
        instruction = prompt or task.contract.objective
        try:
            async for event in self._controller.ask(
                instruction,
                thread_id=task.thread_id,
                cancellation=token,
                task=task,
                attachments=attachments,
            ):
                yield event
        except CancellationError:
            return
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

    async def accept_partial(self, task_id: str, reason: str = "user accepted partial delivery") -> TaskRecord:
        """Record explicit user acceptance without claiming verified completion."""
        task = await self._sessions.load_task(task_id)
        if task.status not in {TaskStatus.VERIFYING, TaskStatus.WAITING_DECISION}:
            raise RuntimeError("only verifying or waiting tasks can be accepted partially")
        accepted = await self._sessions.transition_task(task_id, TaskStatus.ACCEPTED_PARTIAL, reason)
        await self._sessions.create_checkpoint(
            accepted.thread_id,
            "task-accepted-partial",
            {"task_id": accepted.id, "status": accepted.status.value, "reason": reason},
        )
        return accepted

    async def interrupt(self, task_id: str, reason: str = "TUI closed") -> None:
        """Persist an interrupt before the terminal stops its helper coroutines."""
        token = self._tokens.get(task_id)
        if token is not None:
            token.cancel(reason)
        task = await self._sessions.load_task(task_id)
        if task.status in {TaskStatus.RUNNING, TaskStatus.VERIFYING}:
            interrupted = await self._sessions.transition_task(task_id, TaskStatus.INTERRUPTED, reason)
            await self._sessions.create_checkpoint(
                interrupted.thread_id,
                "task-interrupted",
                {"task_id": interrupted.id, "status": interrupted.status.value, "reason": reason},
            )

    async def resume(
        self,
        task_id: str,
        instruction: str = "continue safely",
        *,
        attachments: Sequence[AttachmentRef] = (),
    ) -> AsyncIterator[AgentEvent]:
        async for event in self.events(task_id, instruction, attachments=attachments):
            yield event

    async def steer(
        self,
        task_id: str,
        instruction: str,
        *,
        attachments: Sequence[AttachmentRef] = (),
    ) -> None:
        checked = freeze_attachments(tuple(attachments))
        if (
            not isinstance(instruction, str)
            or len(instruction) > 1024
            or (not instruction.strip() and not checked)
        ):
            raise ValueError("instruction or attachments must be bounded input")
        control = instruction if instruction.strip() else "apply attached user input"
        await self._sessions.record_task_steering(
            task_id,
            Message(role="user", content=instruction, attachments=checked),
            control,
        )

    async def reconcile_stale_tasks(self) -> tuple[str, ...]:
        reconcile = getattr(self._sessions, "reconcile_stale_tasks", None)
        if not callable(reconcile):
            return ()
        return await reconcile(_owner_is_alive)


def _owner_is_alive(owner_pid: int, owner_create_time: float) -> bool:
    try:
        return abs(psutil.Process(owner_pid).create_time() - owner_create_time) < 0.01
    except (psutil.Error, OSError):
        return False
