from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from inspect import signature
from typing import AsyncIterator, Optional

from .cancellation import CancellationError, CancellationToken
from .context_request import ContextRequest, budget_lease
from .errors import AgentEngineError, ContextBuildError, EngineLimitError, ModelStreamError
from .events import AgentEvent, EventKind
from .limits import (
    TaskBudget,
    TaskProgressSnapshot,
    add_usage,
    select_budget_lease,
)
from .models import (
    ActionResult,
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
from .exploration_repeat import ExplorationRepeatObserver, ToolOnlyConvergenceGuard
from .runtime_timing import phase_duration_ms, phase_started_at


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
    exploration_repeat: ExplorationRepeatObserver = field(default_factory=ExplorationRepeatObserver)
    tool_only_guard: ToolOnlyConvergenceGuard = field(default_factory=ToolOnlyConvergenceGuard)
    pending_runtime_notices: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _TurnState:
    number: int
    tools: tuple[ToolDefinition, ...]
    tool_names: set[str]
    summary_only: bool = False
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
            active_thread,
            self._model_name,
            self._limits,
            select_budget_lease(task.contract) if task is not None else None,
        )
        supervisor = TaskSupervisor(task.contract, budget) if task else None
        state = _RunState(active_thread, token, task, budget, supervisor)
        started = AgentEvent(
            kind=EventKind.RUN_STARTED,
            payload={"thread_id": active_thread},
        )
        await self._journal.append_event(active_thread, started)
        return state, started

    async def _task_progress_snapshot(
        self, thread_id: str, budget: TaskBudget
    ) -> TaskProgressSnapshot:
        task_state = await self._journal.load_task_state(thread_id)
        messages = await self._journal.load_messages(thread_id)
        action_fingerprint = ""
        verification_fingerprint = ""
        reason = "initial task state"
        latest_tool = next(
            (message for message in reversed(messages) if message.role == "tool"),
            None,
        )
        if latest_tool is not None:
            result = _tool_result(latest_tool)
            if result is not None and latest_tool.name == "run_verification":
                verification_fingerprint = _message_fingerprint(latest_tool, messages)
                reason = "new verification result"
            elif (
                result is not None
                and not result.is_error
                and latest_tool.name in _READ_PROGRESS_TOOLS
            ):
                action_fingerprint = _message_fingerprint(latest_tool, messages)
                reason = "new read result"
        if not action_fingerprint and not verification_fingerprint:
            if task_state.code_generation:
                reason = "new code generation"
            elif budget.last_failure_signature:
                reason = "new validation failure"
            elif sum(message.role == "user" for message in messages) > 1:
                reason = "task revised by user input"
        return TaskProgressSnapshot(
            code_generation=task_state.code_generation,
            subject_hash=task_state.subject_hash,
            verification_fingerprint=verification_fingerprint,
            failure_fingerprint=budget.last_failure_signature or "",
            action_fingerprint=action_fingerprint,
            interaction_revision=sum(
                message.role == "user" for message in messages
            ),
            reason=reason,
        )

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
                task_facts={**mode_snapshot, **self._context_permission_snapshot},
            )
            bundle = await _invoke_context_builder(self._context, request)
            if not isinstance(bundle, ContextBundle):
                raise TypeError("context builder returned an invalid bundle")
            return bundle
        except (CancellationError, EngineLimitError):
            raise
        except Exception as error:
            # Preserve the causal chain for trusted diagnostics.  The public
            # event still carries only the bounded ContextBuildError label;
            # terminal rendering decides whether/how to summarize the cause.
            raise ContextBuildError("context build failed") from error

    async def _stream_model_events(
        self, state: _RunState, turn: _TurnState, bundle: ContextBundle
    ) -> AsyncIterator[AgentEvent]:
        completed = False
        context_accepted = False
        model_started_at = phase_started_at()
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
            raise ModelStreamError("model stream failed") from exc
        if not completed:
            raise ModelStreamError("model stream ended before completion")
        timing = AgentEvent(
            EventKind.PHASE_COMPLETED,
            {
                "phase": "model",
                "duration_ms": phase_duration_ms(model_started_at),
                "turn": turn.number,
            },
        )
        await self._journal.append_event(state.thread_id, timing)
        yield timing

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
                state.pending_runtime_notices.append(
                    f"Runtime control: {warning.payload['reason']}. "
                    "Use the remaining turn to synthesize the answer; do not "
                    "continue exploratory tool calls unless necessary."
                )
                yield warning
        if state.total_usage.total_tokens > self._limits.max_total_tokens:
            raise EngineLimitError("token budget exceeded")


_READ_PROGRESS_TOOLS = frozenset(
    {
        "read_file",
        "read_code_slices",
        "list_files",
        "search_text",
        "load_tool_contract",
        "git_status",
        "git_diff",
    }
)


def _tool_result(message: Message) -> ActionResult | None:
    try:
        payload = json.loads(message.content)
        if not isinstance(payload, dict):
            return None
        return ActionResult.from_dict(payload)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _message_fingerprint(
    message: Message, messages: tuple[Message, ...] = ()
) -> str:
    result = _tool_result(message)
    if result is None:
        return ""
    arguments: dict[str, object] = {}
    for candidate in reversed(messages):
        if candidate.role != "assistant":
            continue
        call = next(
            (
                item
                for item in candidate.tool_calls
                if item.id == message.tool_call_id and item.name == message.name
            ),
            None,
        )
        if call is not None:
            arguments = dict(call.arguments)
            break
    encoded = json.dumps(
        {
            "name": message.name,
            "arguments": arguments,
            "is_error": result.is_error,
            "output": dict(result.output),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
