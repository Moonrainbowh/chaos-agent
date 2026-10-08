from __future__ import annotations

from typing import AsyncIterator, Protocol, Sequence

from .action_execution import ActionExecutionContext
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
from .limits import (
    BudgetLeaseTier,
    BudgetReservation,
    EngineLimits,
    TaskBudget,
    TaskProgressSnapshot,
)
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
        self,
        request: ActionRequest,
        cancellation: CancellationToken,
        task_authorization: TaskAuthorization | None = None,
        *,
        execution_context: ActionExecutionContext | None = None,
    ) -> ActionResult: ...


class BoundedHistoryRepository(Protocol):
    """Optional production journal queries; legacy repositories remain compatible."""
    async def load_context_messages(self, thread_id: str) -> Sequence[Message]: ...
    async def pending_action_records(self, thread_id: str, *, limit: int = 256) -> Sequence[object]: ...
    async def has_tool_call_id(self, thread_id: str, call_id: str) -> bool: ...
    async def history_stats(self, thread_id: str) -> dict[str, int]: ...
    async def read_history_page(self, thread_id: str, **kwargs: object) -> Sequence[object]: ...
    async def load_host_progress_projection(self, thread_id: str) -> dict[str, object] | None: ...
    async def save_host_progress_projection(self, thread_id: str, payload: dict[str, object], **kwargs: object) -> bool: ...


class SessionRepository(Protocol):
    async def create_thread(
        self, *, parent_thread_id: str | None = None
    ) -> str: ...

    async def load_messages(self, thread_id: str) -> Sequence[Message]: ...

    async def append_message(
        self, thread_id: str, message: Message
    ) -> None: ...

    async def append_event(
        self, thread_id: str, event: AgentEvent
    ) -> None: ...

    async def get_or_create_task_budget(
        self,
        thread_id: str,
        model_name: str,
        limits: EngineLimits,
        lease_tier: BudgetLeaseTier | None = None,
    ) -> TaskBudget: ...

    async def reserve_task_budget(
        self,
        thread_id: str,
        *,
        model_turns: int = 0,
        tool_calls: int = 0,
        progress: TaskProgressSnapshot | None = None,
    ) -> BudgetReservation: ...

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
    async def record_task_steering(
        self, task_id: str, message: Message, instruction: str
    ) -> None: ...
    async def record_task_followup(
        self, task_id: str, message: Message, instruction: str
    ) -> str: ...
    async def promote_task_followups(
        self, task_id: str
    ) -> tuple[tuple[str, Message], ...]: ...
    async def consume_task_controls(self, task_id: str) -> tuple[str, ...]: ...

    async def load_task_state(self, thread_id: str) -> TaskState: ...

    async def save_task_state(self, thread_id: str, state: TaskState) -> None: ...

    async def reduce_task_state(
        self, thread_id: str, request: ActionRequest, result: ActionResult
    ) -> TaskState: ...
