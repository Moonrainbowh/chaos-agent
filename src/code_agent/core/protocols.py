from __future__ import annotations

from typing import AsyncIterator, Protocol, Sequence

from .cancellation import CancellationToken
from .events import AgentEvent
from .models import (
    ActionRequest,
    ActionResult,
    ContextBundle,
    Message,
    ModelEvent,
    ToolDefinition,
)
from .limits import EngineLimits, TaskBudget


class ModelClient(Protocol):
    def stream(
        self,
        system_prompt: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[ModelEvent]: ...


class ContextBuilder(Protocol):
    async def build(
        self, messages: Sequence[Message], user_input: str
    ) -> ContextBundle: ...


class ActionDispatcher(Protocol):
    def tools(self) -> Sequence[ToolDefinition]: ...

    async def dispatch(
        self, request: ActionRequest, cancellation: CancellationToken
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
