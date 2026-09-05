from __future__ import annotations

from dataclasses import dataclass, field
from inspect import signature
from typing import AsyncIterator, Optional

from .cancellation import CancellationError, CancellationToken
from .context_request import ContextRequest, budget_lease
from .errors import AgentEngineError, ContextBuildError, EngineLimitError, ModelStreamError
from .events import AgentEvent, EventKind
from .limits import TaskBudget, add_usage
from .models import (
    ContextBundle,
    Message,
    ModelEventKind,
    ToolCall,
    ToolDefinition,
    Usage,
)
from .attachments import AttachmentRef, freeze_attachments
from .task import TaskRecord, TaskStatus
from .task_supervisor import TaskSupervisor


@dataclass(slots=True)
class _RunState:
    thread_id: str
    token: CancellationToken
    task: TaskRecord | None
    budget: TaskBudget
    supervisor: TaskSupervisor | None
    prior_messages: tuple[Message, ...] = ()
    messages: tuple[Message, ...] = ()
    used_call_ids: set[str] = field(default_factory=set)
    total_usage: Usage = field(default_factory=Usage)
    stop_requested: bool = False
    allowed_tool_names: frozenset[str] | None = None
    disclosed_tool_digests: dict[str, str] = field(default_factory=dict)
    action_history: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _TurnState:
    number: int
    tools: tuple[ToolDefinition, ...]
    tool_names: set[str]
    text_parts: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)


def _validate_run_arguments(
    user_input: str,
    thread_id: Optional[str],
    attachments: tuple[AttachmentRef, ...] = (),
) -> tuple[AttachmentRef, ...]:
    if not isinstance(user_input, str):
        raise TypeError("user_input must be a string")
    checked = freeze_attachments(attachments)
    if not user_input.strip() and not checked:
        raise ValueError("user_input and attachments must not both be blank")
    if thread_id is not None and (
        not isinstance(thread_id, str) or not thread_id.strip()
    ):
        raise ValueError("thread_id must be a non-blank string or None")
    return checked


async def _invoke_context_builder(
    builder: object, request: ContextRequest
) -> ContextBundle:
    build = getattr(builder, "build", None)
    if not callable(build):
        raise TypeError("context builder must provide build")
    try:
        build_signature = signature(build)
    except (TypeError, ValueError):
        accepts_request = True
    else:
        try:
            build_signature.bind(request)
        except TypeError:
            accepts_request = False
        else:
            accepts_request = True
    if accepts_request:
        return await build(request)
    return await build(
        request.thread_id,
        request.messages,
        request.user_input,
        request.tools,
        request.task_state,
        request.cancellation,
    )


class AgentEngineRunMixin:
    """Prepare one run and stream model events into its mutable state."""

    async def _handle_run_failure(self, state, error):
        if isinstance(error, EngineLimitError) and state.task is not None:
            reason = str(error)
            await self._journal.transition_task(state.task.id, TaskStatus.PAUSED, reason)
            event = AgentEvent(EventKind.TASK_PAUSED, {"task_id": state.task.id,
                "status": "paused", "reason": reason})
        else:
            event = AgentEvent(EventKind.ERROR, {"code": error.code, "error_type": type(error).__name__})
        await self._journal.append_event(state.thread_id, event)
        yield event

    async def _start_run(
        self,
        thread_id: Optional[str],
        cancellation: Optional[CancellationToken],
        task: TaskRecord | None,
    ) -> tuple[_RunState, AgentEvent]:
        token = cancellation or CancellationToken()
        active_thread = thread_id or await self._journal.create_thread()
        if task is not None and task.thread_id != active_thread:
            raise ValueError("task must belong to the active thread")
        budget = await self._journal.get_or_create_task_budget(
            active_thread, self._model_name, self._limits
        )
        supervisor = TaskSupervisor(task.contract, budget) if task else None
        state = _RunState(active_thread, token, task, budget, supervisor)
        started = AgentEvent(
            kind=EventKind.RUN_STARTED,
            payload={"thread_id": active_thread},
        )
        await self._journal.append_event(active_thread, started)
        return state, started

    async def _prepare_request(
        self,
        state: _RunState,
        user_input: str,
        attachments: tuple[AttachmentRef, ...] = (),
    ) -> tuple[AgentEvent, Message]:
        state.token.raise_if_cancelled()
        state.prior_messages = await self._journal.load_messages(state.thread_id)
        if state.task is not None and self._verification is not None:
            prepared = await self._verification.prepare(
                state.task, await self._journal.load_task_state(state.thread_id)
            )
            await self._journal.save_task_state(state.thread_id, prepared)
        user_message = Message(
            role="user", content=user_input, attachments=attachments
        )
        await self._journal.append_message(state.thread_id, user_message)
        added = self._journal.message_added(user_message)
        await self._journal.append_event(state.thread_id, added)
        return added, user_message

    async def _build_turn_context(
        self, state: _RunState, turn: _TurnState, user_input: str
    ) -> ContextBundle:
        source_messages = (
            await self._journal.load_messages(state.thread_id)
            if state.task is not None
            else state.messages
        )
        source_input = ""
        try:
            task_state = await self._journal.load_task_state(state.thread_id)
            mode_snapshot = dict(self._context_mode_snapshot)
            if state.task is not None:
                mode_snapshot["interaction_mode"] = state.task.contract.interaction_mode
            request = ContextRequest(
                thread_id=state.thread_id,
                revision=state.budget.model_turns,
                messages=source_messages,
                user_input=source_input,
                tools=turn.tools,
                task_state=task_state,
                cancellation=state.token,
                mode_snapshot=mode_snapshot,
                permission_snapshot=self._context_permission_snapshot,
                budget_lease=budget_lease(state.budget),
            )
            bundle = await _invoke_context_builder(self._context, request)
            if not isinstance(bundle, ContextBundle):
                raise TypeError("context builder returned an invalid bundle")
            return bundle
        except (CancellationError, EngineLimitError):
            raise
        except Exception:
            raise ContextBuildError("context build failed") from None

    async def _stream_model_events(
        self, state: _RunState, turn: _TurnState, bundle: ContextBundle
    ) -> AsyncIterator[AgentEvent]:
        completed = False
        context_accepted = False
        try:
            stream = self._model.stream(
                bundle.system_prompt, bundle.messages, turn.tools
            )
            async for model_event in stream:
                state.token.raise_if_cancelled()
                if completed:
                    raise ModelStreamError(
                        "model emitted an event after completion"
                    )
                self._accumulate_model_event(
                    model_event, turn.text_parts, turn.calls
                )
                if model_event.kind is ModelEventKind.COMPLETED:
                    completed = True
                streamed = AgentEvent(
                    EventKind.MODEL_EVENT,
                    {"event": model_event.to_dict()},
                )
                await self._journal.append_event(state.thread_id, streamed)
                if not context_accepted:
                    accept = getattr(
                        self._context, "accept_pending_context", None
                    )
                    if callable(accept):
                        await accept(state.thread_id)
                    context_accepted = True
                yield streamed
                if model_event.usage is not None:
                    async for warning in self._record_model_usage(
                        state, model_event.usage
                    ):
                        yield warning
        except (AgentEngineError, CancellationError):
            raise
        except Exception as exc:
            import traceback
            traceback.print_exc()
            raise ModelStreamError(f"model stream failed: {exc}") from exc
        if not completed:
            raise ModelStreamError("model stream ended before completion")

    async def _record_model_usage(
        self, state: _RunState, usage: Usage
    ) -> AsyncIterator[AgentEvent]:
        state.total_usage = add_usage(state.total_usage, usage)
        if state.task is not None:
            await self._journal.consume_task_usage(state.task.id, usage)
            thresholds = await self._journal.mark_task_budget_warnings(
                state.task.id
            )
            for threshold in thresholds:
                warning = AgentEvent(
                    EventKind.TASK_BUDGET_WARNING,
                    {
                        "task_id": state.task.id,
                        "threshold": threshold,
                        "reason": f"token budget reached {threshold}%",
                    },
                )
                await self._journal.append_event(state.thread_id, warning)
                yield warning
        if state.total_usage.total_tokens > self._limits.max_total_tokens:
            raise EngineLimitError("token budget exceeded")
