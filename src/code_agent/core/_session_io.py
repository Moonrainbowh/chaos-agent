from __future__ import annotations

import hashlib

from .errors import SessionPersistenceError
from .events import AgentEvent, EventKind
from .models import ActionRequest, ActionResult, Message
from .models import Usage
from .limits import (
    BudgetLeaseTier,
    BudgetReservation,
    EngineLimits,
    TaskBudget,
    TaskProgressSnapshot,
)
from .protocols import SessionRepository
from .task_state import TaskState
from .task import TaskRecord, TaskStatus
from .debug_trace import trace_event


class SessionJournal:
    """Turns repository failures into engine-safe persistence errors."""

    def __init__(self, repository: SessionRepository) -> None:
        self._repository = repository

    async def create_thread(self) -> str:
        try:
            return await self._repository.create_thread()
        except Exception:
            raise SessionPersistenceError("could not create session") from None

    async def load_task_for_thread(self, thread_id: str) -> TaskRecord | None:
        try:
            task = await self._repository.load_task_for_thread(thread_id)
            if task is not None and not isinstance(task, TaskRecord):
                raise TypeError("session has invalid task")
            return task
        except Exception:
            raise SessionPersistenceError("could not load task") from None

    async def load_messages(self, thread_id: str) -> tuple[Message, ...]:
        try:
            messages = tuple(await self._repository.load_messages(thread_id))
            if not all(isinstance(message, Message) for message in messages):
                raise TypeError("session has invalid messages")
            return messages
        except Exception:
            raise SessionPersistenceError("could not load session") from None

    async def load_context_messages(self, thread_id):
        read = getattr(self._repository, "load_context_messages", None)
        if not callable(read):
            return await self.load_messages(thread_id)
        try:
            return tuple(await read(thread_id))
        except Exception:
            raise SessionPersistenceError("required context history cannot fit bounded page") from None

    async def has_pending_actions(self, thread_id):
        read = getattr(self._repository, "pending_action_records", None)
        if not callable(read):
            from .pending_actions import pending_calls
            return bool(pending_calls(await self.load_messages(thread_id)))
        try:
            return bool(await read(thread_id))
        except Exception:
            raise SessionPersistenceError("could not inspect durable pending actions") from None

    async def has_tool_call_id(self, thread_id, call_id):
        read = getattr(self._repository, "has_tool_call_id", None)
        if not callable(read):
            return any(call.id==call_id for message in await self.load_messages(thread_id) for call in message.tool_calls)
        try:
            return await read(thread_id,call_id)
        except Exception:
            raise SessionPersistenceError("could not inspect durable call identity") from None

    async def host_progress(self, thread_id, objective, dispatcher, *, candidate_limit, hard_tool_limit):
        from .host_progress import observe_host_progress, interaction_revision, fingerprint
        store = self._repository
        if not callable(getattr(store, "load_host_progress_projection", None)):
            messages = await self.load_messages(thread_id)
            return observe_host_progress(messages, objective, dispatcher,
                candidate_limit=candidate_limit,hard_tool_limit=hard_tool_limit), interaction_revision(messages)
        try:
            for _ in range(3):
                stats = await store.history_stats(thread_id)
                prior = await store.load_host_progress_projection(thread_id)
                epoch_valid = prior is not None and prior["epoch"]==stats["message_epoch"]
                if epoch_valid and prior['cursor']>stats['message_sequence']:
                    raise ValueError("progress cursor exceeds durable history")
                valid = epoch_valid and prior['state'].get('identity')==fingerprint((objective,candidate_limit,hard_tool_limit))
                cursor = prior["cursor"] if valid else 0
                # A lease can change candidate limits. Rebuild facts boundedly,
                # but compare the replacement against the durable old cursor.
                start = prior['cursor'] if epoch_valid else 0
                projection = prior["state"] if valid else {}
                facts = observe_host_progress((),objective,dispatcher,candidate_limit=candidate_limit,
                    hard_tool_limit=hard_tool_limit,projection=projection)
                while cursor<stats["message_sequence"]:
                    page = await store.read_history_page(thread_id,after_sequence=cursor,
                        before_sequence=stats["message_sequence"]+1,limit=100,max_bytes=1048576)
                    if not page:
                        raise ValueError("history cursor missing durable source")
                    facts = observe_host_progress(tuple(r.message for r in page),objective,dispatcher,
                        candidate_limit=candidate_limit,hard_tool_limit=hard_tool_limit,projection=projection)
                    cursor=page[-1].sequence
                if valid and cursor==start:
                    current = await store.history_stats(thread_id)
                    if (current['message_epoch'],current['message_revision']) != (stats['message_epoch'],stats['message_revision']):
                        continue
                    return facts,len(projection.get("user_revisions",()))
                if await store.save_host_progress_projection(thread_id,{"cursor":cursor,"state":projection},
                        expected_cursor=start,expected_epoch=stats["message_epoch"]):
                    return facts,len(projection.get("user_revisions",()))
            raise ValueError("history projection changed concurrently")
        except Exception:
            raise SessionPersistenceError("could not project bounded Host progress") from None

    async def append_message(self, thread_id: str, message: Message) -> None:
        try:
            await self._repository.append_message(thread_id, message)
            trace_event(
                "durable.message",
                "appended",
                thread_id=thread_id,
                role=message.role,
                content_chars=len(message.content),
                content_digest=hashlib.sha256(message.content.encode("utf-8")).hexdigest()[:16],
                tool_calls=tuple(call.name for call in message.tool_calls),
            )
        except Exception:
            raise SessionPersistenceError("could not persist message") from None

    async def append_event(self, thread_id: str, event: AgentEvent) -> None:
        try:
            await self._repository.append_event(thread_id, event)
            trace_event(
                "durable.event",
                "appended",
                thread_id=thread_id,
                event_kind=event.kind.value,
                event_turn=event.payload.get("turn"),
                event_sequence=event.payload.get("sequence"),
                payload_keys=tuple(sorted(event.payload)[:16]),
            )
        except Exception:
            raise SessionPersistenceError("could not persist event") from None

    async def get_or_create_task_budget(
        self,
        thread_id: str,
        model_name: str,
        limits: EngineLimits,
        lease_tier: BudgetLeaseTier | None = None,
    ) -> TaskBudget:
        try:
            return await self._repository.get_or_create_task_budget(
                thread_id, model_name, limits, lease_tier
            )
        except Exception:
            raise SessionPersistenceError("could not load task budget") from None

    async def reserve_task_budget(
        self,
        thread_id: str,
        *,
        model_turns: int = 0,
        tool_calls: int = 0,
        progress: TaskProgressSnapshot | None = None,
    ) -> BudgetReservation:
        try:
            return await self._repository.reserve_task_budget(
                thread_id,
                model_turns=model_turns,
                tool_calls=tool_calls,
                progress=progress,
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

    async def mark_task_budget_warnings(self, task_id: str) -> tuple[int, ...]:
        try:
            thresholds = await self._repository.mark_task_budget_warnings(task_id)
            if not all(value in {80, 90} for value in thresholds):
                raise TypeError("session returned invalid budget warning thresholds")
            return thresholds
        except Exception:
            raise SessionPersistenceError("could not persist task budget warnings") from None

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

    async def promote_task_followups(
        self, task_id: str
    ) -> tuple[tuple[str, Message], ...]:
        try:
            promoted = tuple(
                await self._repository.promote_task_followups(task_id)
            )
            if not all(
                isinstance(identifier, str) and isinstance(message, Message)
                for identifier, message in promoted
            ):
                raise TypeError("promoted follow-ups must contain ids and messages")
            return promoted
        except Exception:
            raise SessionPersistenceError("could not promote task follow-ups") from None

    @staticmethod
    def message_added(message: Message) -> AgentEvent:
        return AgentEvent(
            kind=EventKind.MESSAGE_ADDED,
            payload={"message": message.to_dict()},
        )
