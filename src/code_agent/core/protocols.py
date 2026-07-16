from __future__ import annotations

from typing import AsyncIterator, Protocol, Sequence

from .cancellation import CancellationToken
from .context_request import ContextRequest
from .events import AgentEvent
from .models import (
    ActionRequest,
    ActionResult,
    ContextBundle,
    Message,
    ModelEvent,
    ToolDefinition,
    Usage,
)
from .task_state import TaskState
from .limits import EngineLimits, TaskBudget
from .task import TaskAuthorization, TaskContract, TaskRecord, TaskStatus


class ModelClient(Protocol):
    def stream(
        self,
        system_prompt: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[ModelEvent]: ...


class ContextBuilder(Protocol):
    async def build(self, request: ContextRequest) -> ContextBundle: ...


class ActionDispatcher(Protocol):
    def tools(self) -> Sequence[ToolDefinition]: ...

    async def dispatch(
        self, request: ActionRequest, cancellation: CancellationToken,
        task_authorization: TaskAuthorization | None = None,
    ) -> ActionResult: ...


class SessionRepository(Protocol):
    async def create_thread(self) -> str: ...

    async def load_messages(self, thread_id: str) -> Sequence[Message]: ...

    async def append_message(
        self, thread_id: str, message: Message
    ) -> None: ...

    async def append_event(
        self, thread_id: str, event: AgentEvent
    ) -> None: ...

    async def get_or_create_task_budget(
        self, thread_id: str, model_name: str, limits: EngineLimits
    ) -> TaskBudget: ...

    async def reserve_task_budget(
        self, thread_id: str, *, model_turns: int = 0, tool_calls: int = 0
    ) -> TaskBudget | None: ...

    async def create_task(self, thread_id: str, contract: TaskContract) -> TaskRecord: ...
    async def load_task(self, task_id: str) -> TaskRecord: ...
    async def load_task_for_thread(self, thread_id: str) -> TaskRecord | None: ...
    async def transition_task(self, task_id: str, status: TaskStatus, reason: str | None = None) -> TaskRecord: ...
    async def list_tasks(self, *, include_terminal: bool = False) -> tuple[TaskRecord, ...]: ...
    async def consume_task_usage(self, task_id: str, usage: Usage) -> TaskBudget: ...
    async def mark_task_budget_warnings(self, task_id: str) -> tuple[int, ...]: ...
    async def observe_task_validation(self, task_id: str, fingerprint: str | None, changed_files: int) -> TaskBudget: ...
    async def record_task_active_seconds(self, task_id: str, active_seconds: int) -> TaskBudget: ...
    async def record_task_control(self, task_id: str, instruction: str) -> None: ...
    async def consume_task_controls(self, task_id: str) -> tuple[str, ...]: ...

    async def load_task_state(self, thread_id: str) -> TaskState: ...

    async def save_task_state(self, thread_id: str, state: TaskState) -> None: ...

    async def reduce_task_state(
        self, thread_id: str, request: ActionRequest, result: ActionResult
    ) -> TaskState: ...
