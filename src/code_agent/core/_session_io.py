from __future__ import annotations

from .errors import SessionPersistenceError
from .events import AgentEvent, EventKind
from .models import ActionRequest, ActionResult, Message
from .models import Usage
from .limits import EngineLimits, TaskBudget
from .protocols import SessionRepository
from .task_state import TaskState
from .task import TaskRecord, TaskStatus


class SessionJournal:
    """Turns repository failures into engine-safe persistence errors."""

    def __init__(self, repository: SessionRepository) -> None:
        self._repository = repository

    async def create_thread(self) -> str:
        try:
            return await self._repository.create_thread()
        except Exception:
            raise SessionPersistenceError("could not create session") from None

    async def load_messages(self, thread_id: str) -> tuple[Message, ...]:
        try:
            messages = tuple(await self._repository.load_messages(thread_id))
            if not all(isinstance(message, Message) for message in messages):
                raise TypeError("session has invalid messages")
            return messages
        except Exception:
            raise SessionPersistenceError("could not load session") from None

    async def append_message(self, thread_id: str, message: Message) -> None:
        try:
            await self._repository.append_message(thread_id, message)
        except Exception:
            raise SessionPersistenceError("could not persist message") from None

    async def append_event(self, thread_id: str, event: AgentEvent) -> None:
        try:
            await self._repository.append_event(thread_id, event)
        except Exception:
            raise SessionPersistenceError("could not persist event") from None

    async def get_or_create_task_budget(
        self, thread_id: str, model_name: str, limits: EngineLimits
    ) -> TaskBudget:
        try:
            return await self._repository.get_or_create_task_budget(thread_id, model_name, limits)
        except Exception:
            raise SessionPersistenceError("could not load task budget") from None

    async def reserve_task_budget(
        self, thread_id: str, *, model_turns: int = 0, tool_calls: int = 0
    ) -> TaskBudget | None:
        try:
            return await self._repository.reserve_task_budget(
                thread_id, model_turns=model_turns, tool_calls=tool_calls
            )
        except Exception:
            raise SessionPersistenceError("could not persist task budget") from None

    async def load_task_state(self, thread_id: str) -> TaskState:
        try:
            state = await self._repository.load_task_state(thread_id)
            if not isinstance(state, TaskState):
                raise TypeError("session has invalid task state")
            return state
        except Exception:
            raise SessionPersistenceError("could not load task state") from None

    async def save_task_state(self, thread_id: str, state: TaskState) -> None:
        try:
            await self._repository.save_task_state(thread_id, state)
        except Exception:
            raise SessionPersistenceError("could not persist task state") from None

    async def reduce_task_state(
        self, thread_id: str, request: ActionRequest, result: ActionResult
    ) -> TaskState:
        try:
            state = await self._repository.reduce_task_state(thread_id, request, result)
            if not isinstance(state, TaskState):
                raise TypeError("session has invalid task state")
            return state
        except Exception:
            raise SessionPersistenceError("could not persist task state") from None

    async def transition_task(self, task_id: str, status: TaskStatus, reason: str | None = None) -> TaskRecord:
        try:
            return await self._repository.transition_task(task_id, status, reason)
        except Exception:
            raise SessionPersistenceError("could not transition task") from None

    async def create_checkpoint(self, thread_id: str, label: str, metadata: dict[str, object]) -> object:
        try:
            return await self._repository.create_checkpoint(thread_id, label, metadata)
        except Exception:
            raise SessionPersistenceError("could not create task checkpoint") from None

    async def consume_task_usage(self, task_id: str, usage: Usage) -> TaskBudget:
        try:
            return await self._repository.consume_task_usage(task_id, usage)
        except Exception:
            raise SessionPersistenceError("could not persist task usage") from None

    async def observe_task_validation(self, task_id: str, fingerprint: str | None, changed_files: int) -> TaskBudget:
        try:
            return await self._repository.observe_task_validation(task_id, fingerprint, changed_files)
        except Exception:
            raise SessionPersistenceError("could not persist task validation") from None

    async def record_task_active_seconds(self, task_id: str, active_seconds: int) -> TaskBudget:
        try:
            return await self._repository.record_task_active_seconds(task_id, active_seconds)
        except Exception:
            raise SessionPersistenceError("could not persist task active time") from None

    async def consume_task_controls(self, task_id: str) -> tuple[str, ...]:
        try:
            controls = await self._repository.consume_task_controls(task_id)
            if not all(isinstance(control, str) for control in controls):
                raise TypeError("task controls must be text")
            return controls
        except Exception:
            raise SessionPersistenceError("could not consume task controls") from None

    @staticmethod
    def message_added(message: Message) -> AgentEvent:
        return AgentEvent(
            kind=EventKind.MESSAGE_ADDED,
            payload={"message": message.to_dict()},
        )
