"""Narrow frontend contract implemented by the existing foreground controller."""
from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from code_agent.core.attachments import AttachmentRef
from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import AgentEvent
from code_agent.core.task import TaskRecord
from code_agent.core.task_result import TaskResult


class TaskService(Protocol):
    """Persistent tasks are authoritative; session and attempt views are projections."""

    async def start(self, prompt: str, *, thread_id: str | None = None,
                    source_thread_id: str | None = None) -> TaskRecord: ...

    def events(self, task_id: str, prompt: str | None = None, *,
               attachments: Sequence[AttachmentRef] = (),
               cancellation: CancellationToken | None = None) -> AsyncIterator[AgentEvent]: ...

    async def pause(self, task_id: str, reason: str = "user requested pause") -> None: ...

    async def interrupt(self, task_id: str, reason: str = "client disconnected") -> None: ...

    async def accept_partial(self, task_id: str,
                             reason: str = "user accepted partial delivery") -> TaskRecord: ...

    async def recovery_checklist(self, task_id: str) -> dict[str, object]: ...

    async def resolve_pending_action(self, task_id: str, **decision) -> AgentEvent: ...

    async def result(self, task_id: str) -> TaskResult: ...
