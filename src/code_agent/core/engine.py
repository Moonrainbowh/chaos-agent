from __future__ import annotations

import json
from collections.abc import Mapping
from typing import AsyncIterator, Optional

from ._session_io import SessionJournal
from ._tool_feedback import tool_failure
from .cancellation import CancellationError, CancellationToken
from .errors import (
    AgentEngineError,
    ContextBuildError,
    EngineLimitError,
    ModelStreamError,
)
from .events import AgentEvent, EventKind
from .limits import EngineLimits, add_usage, usage_payload
from .models import (
    ActionRequest,
    ContextBundle,
    Message,
    ModelEvent,
    ModelEventKind,
    ToolCall,
    ToolDefinition,
    Usage,
)
from .protocols import (
    ActionDispatcher,
    ContextBuilder,
    ModelClient,
    SessionRepository,
)
from .task_state import TaskState
from .task import TaskRecord
from .task import TaskStatus
from .task_supervisor import SupervisionKind, TaskSupervisor


class AgentEngine:
    def __init__(
        self,
        model: ModelClient,
        context: ContextBuilder,
        actions: ActionDispatcher,
        sessions: SessionRepository,
        *,
        limits: Optional[EngineLimits] = None,
        model_name: str = "configured-model",
    ) -> None:
        self._model = model
        self._context = context
        self._actions = actions
        self._journal = SessionJournal(sessions)
        self._limits = limits or EngineLimits()
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("model_name must be non-blank text")
        self._model_name = model_name

    async def run(
        self,
        user_input: str,
        *,
        thread_id: Optional[str] = None,
        cancellation: Optional[CancellationToken] = None,
        task: TaskRecord | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run one user request and stream events after durable persistence."""
        if not isinstance(user_input, str):
            raise TypeError("user_input must be a string")
        if not user_input.strip():
            raise ValueError("user_input must not be blank")
        if thread_id is not None and (
            not isinstance(thread_id, str) or not thread_id.strip()
        ):
            raise ValueError("thread_id must be a non-blank string or None")

        token = cancellation or CancellationToken()
        active_thread = thread_id or await self._journal.create_thread()
        if task is not None and task.thread_id != active_thread:
            raise ValueError("task must belong to the active thread")
        task_budget = await self._journal.get_or_create_task_budget(
            active_thread, self._model_name, self._limits
        )
        supervisor = TaskSupervisor(task.contract, task_budget) if task else None
        started = AgentEvent(
            kind=EventKind.RUN_STARTED,
            payload={"thread_id": active_thread},
        )
        await self._journal.append_event(active_thread, started)
        yield started

        try:
            token.raise_if_cancelled()
            prior_messages = await self._journal.load_messages(active_thread)
            user_message = Message(role="user", content=user_input)
            await self._journal.append_message(active_thread, user_message)
            added = self._journal.message_added(user_message)
            await self._journal.append_event(active_thread, added)
            yield added

            messages = prior_messages + (user_message,)
            used_call_ids: set[str] = set()
            total_usage = Usage()

            for turn in range(1, task_budget.limits.max_agent_rounds + 1):
                token.raise_if_cancelled()
                if supervisor is not None:
                    decision = supervisor.before_model_turn()
                    if decision.kind is SupervisionKind.PAUSE:
                        await self._pause_task(active_thread, task, supervisor, decision.reason or "task paused")
                        paused = AgentEvent(EventKind.TASK_PAUSED, {"task_id": task.id, "status": "paused", "reason": decision.reason or "task paused"})
                        await self._journal.append_event(active_thread, paused)
                        yield paused
                        return
                    await self._journal.record_task_active_seconds(
                        task.id, supervisor.checkpoint_active_seconds()
                    )
                    await self._journal.consume_task_controls(task.id)
                reserved = await self._journal.reserve_task_budget(
                    active_thread, model_turns=1
                )
                if reserved is None:
                    raise EngineLimitError("model turn budget exceeded")
                task_budget = reserved
                tools, tool_names = self._advertised_tools()
                turn_started = AgentEvent(
                    kind=EventKind.TURN_STARTED,
                    payload={"turn": turn},
                )
                await self._journal.append_event(active_thread, turn_started)
                yield turn_started

                source_messages = (
                    await self._journal.load_messages(active_thread)
                    if task is not None
                    else prior_messages if turn == 1 else messages
                )
                source_input = user_input if turn == 1 else ""
                try:
                    task_state = await self._journal.load_task_state(active_thread)
                    bundle = await self._context.build(
                        source_messages,
                        source_input,
                        tools,
                        task_state,
                    )
                    if not isinstance(bundle, ContextBundle):
                        raise TypeError("context builder returned an invalid bundle")
                except CancellationError:
                    raise
                except Exception:
                    raise ContextBuildError("context build failed") from None

                built = AgentEvent(
                    kind=EventKind.CONTEXT_BUILT,
                    payload={"turn": turn, **bundle.measurements},
                )
                await self._journal.append_event(active_thread, built)
                yield built

                model_started = AgentEvent(
                    kind=EventKind.MODEL_STARTED,
                    payload={"turn": turn},
                )
                await self._journal.append_event(active_thread, model_started)
                yield model_started

                text_parts: list[str] = []
                calls: list[ToolCall] = []
                completed = False
                try:
                    stream = self._model.stream(
                        bundle.system_prompt,
                        bundle.messages,
                        tools,
                    )
                    async for model_event in stream:
                        token.raise_if_cancelled()
                        if completed:
                            raise ModelStreamError(
                                "model emitted an event after completion"
                            )
                        self._accumulate_model_event(
                            model_event,
                            text_parts,
                            calls,
                        )
                        if model_event.kind is ModelEventKind.COMPLETED:
                            completed = True
                        streamed = AgentEvent(
                            kind=EventKind.MODEL_EVENT,
                            payload={"event": model_event.to_dict()},
                        )
                        await self._journal.append_event(active_thread, streamed)
                        yield streamed
                        if model_event.usage is not None:
                            total_usage = add_usage(total_usage, model_event.usage)
                            if task is not None:
                                await self._journal.consume_task_usage(task.id, model_event.usage)
                            if total_usage.total_tokens > self._limits.max_total_tokens:
                                raise EngineLimitError("token budget exceeded")
                except (AgentEngineError, CancellationError):
                    raise
                except Exception:
                    raise ModelStreamError("model stream failed") from None

                if not completed:
                    raise ModelStreamError("model stream ended before completion")

                assistant = Message(
                    role="assistant",
                    content="".join(text_parts),
                    tool_calls=tuple(calls),
                )
                await self._journal.append_message(active_thread, assistant)
                messages += (assistant,)
                assistant_added = self._journal.message_added(assistant)
                await self._journal.append_event(active_thread, assistant_added)
                yield assistant_added

                if not calls:
                    if task is not None:
                        if supervisor is not None:
                            await self._journal.record_task_active_seconds(
                                task.id, supervisor.checkpoint_active_seconds()
                            )
                        completed_task = await self._journal.transition_task(task.id, TaskStatus.COMPLETED)
                        task_event = AgentEvent(EventKind.TASK_STATUS_CHANGED, {"task_id": completed_task.id, "status": completed_task.status.value})
                        await self._journal.append_event(active_thread, task_event)
                        yield task_event
                    finished = AgentEvent(
                        kind=EventKind.COMPLETED,
                        payload={
                            "thread_id": active_thread,
                            "turns": task_budget.model_turns,
                            "tool_calls": task_budget.tool_calls,
                            "usage": usage_payload(total_usage),
                        },
                    )
                    await self._journal.append_event(active_thread, finished)
                    yield finished
                    return

                if task_budget.model_turns >= task_budget.limits.max_agent_rounds:
                    raise EngineLimitError("model turn budget exceeded")
                if len(calls) > task_budget.limits.max_tool_calls_per_round:
                    raise EngineLimitError("tool call per-round budget exceeded")
                reserved = await self._journal.reserve_task_budget(
                    active_thread, tool_calls=len(calls)
                )
                if reserved is None:
                    raise EngineLimitError("tool call budget exceeded")
                task_budget = reserved
                if len({call.id for call in calls}) != len(calls) or any(
                    call.id in used_call_ids for call in calls
                ):
                    raise ModelStreamError("model reused a tool call id")

                for call in calls:
                    used_call_ids.add(call.id)
                    async for action_event in self._dispatch(
                        active_thread,
                        call,
                        token,
                        is_available=call.name in tool_names,
                        task=task,
                        supervisor=supervisor,
                    ):
                        if action_event.kind is EventKind.MESSAGE_ADDED:
                            result_message = Message.from_dict(
                                action_event.payload["message"]  # type: ignore[arg-type]
                            )
                            messages += (result_message,)
                        yield action_event
                        if action_event.kind in {
                            EventKind.TASK_PAUSED,
                            EventKind.TASK_DECISION_REQUIRED,
                        }:
                            return

            raise EngineLimitError("model turn budget exceeded")
        except CancellationError as exc:
            cancelled = AgentEvent(
                kind=EventKind.CANCELLED,
                payload={"reason": exc.reason},
            )
            await self._journal.append_event(active_thread, cancelled)
            yield cancelled
        except AgentEngineError as exc:
            failed = AgentEvent(
                kind=EventKind.ERROR,
                payload={"code": exc.code, "error_type": type(exc).__name__},
            )
            await self._journal.append_event(active_thread, failed)
            yield failed
            raise

    async def _dispatch(
        self,
        thread_id: str,
        call: ToolCall,
        token: CancellationToken,
        *,
        is_available: bool,
        task: TaskRecord | None = None,
        supervisor: TaskSupervisor | None = None,
    ) -> AsyncIterator[AgentEvent]:
        request = ActionRequest(id=call.id, name=call.name, arguments=call.arguments)
        requested = AgentEvent(
            kind=EventKind.ACTION_REQUESTED,
            payload={"request": request.to_dict()},
        )
        await self._journal.append_event(thread_id, requested)
        yield requested
        if not is_available:
            result = tool_failure(call, "tool is not available")
        else:
            if supervisor is not None and call.name in {"write_file", "replace_text", "run_command"}:
                decision = supervisor.before_external_action()
                if decision.kind is SupervisionKind.PAUSE:
                    await self._pause_task(thread_id, task, supervisor, decision.reason or "task paused")
                    paused = AgentEvent(EventKind.TASK_PAUSED, {"task_id": task.id, "status": "paused", "reason": decision.reason or "task paused"})
                    await self._journal.append_event(thread_id, paused)
                    yield paused
                    return
                await self._journal.record_task_active_seconds(
                    task.id, supervisor.checkpoint_active_seconds()
                )
            started = AgentEvent(
                kind=EventKind.ACTION_STARTED,
                payload={"request_id": call.id, "name": call.name},
            )
            await self._journal.append_event(thread_id, started)
            yield started
            try:
                if task is None:
                    result = await self._actions.dispatch(request, token)
                else:
                    result = await self._actions.dispatch(request, token, task.contract.authorization)
                if result.request_id != call.id or result.name != call.name:
                    result = tool_failure(call, "invalid tool result")
            except CancellationError:
                raise
            except Exception as exc:
                result = tool_failure(
                    call,
                    "tool execution failed",
                    type(exc).__name__,
                )

        if task is not None and _requires_decision(result):
            waiting = await self._journal.transition_task(
                task.id, TaskStatus.WAITING_DECISION, "approval required"
            )
            decision_event = AgentEvent(
                EventKind.TASK_DECISION_REQUIRED,
                {"task_id": waiting.id, "status": waiting.status.value},
            )
            await self._journal.append_event(thread_id, decision_event)
            yield decision_event
            return

        if call.name in {
            "read_file",
            "list_files",
            "search_text",
            "write_file",
            "replace_text",
            "run_command",
        }:
            state = await self._journal.reduce_task_state(thread_id, request, result)
            if task is not None and supervisor is not None and call.name == "run_command":
                fingerprint = _validation_fingerprint(request, result)
                decision = supervisor.observe_validation(fingerprint, len(state.files_changed))
                await self._journal.observe_task_validation(task.id, fingerprint, len(state.files_changed))
                await self._journal.create_checkpoint(
                    thread_id,
                    "validation-complete",
                    {"task_id": task.id, "failed": fingerprint is not None},
                )
                if decision.kind is SupervisionKind.PAUSE:
                    await self._pause_task(thread_id, task, supervisor, decision.reason or "validation paused")
                    paused = AgentEvent(EventKind.TASK_PAUSED, {"task_id": task.id, "status": "paused", "reason": decision.reason or "validation paused"})
                    await self._journal.append_event(thread_id, paused)
                    yield paused

        completed = AgentEvent(
            kind=EventKind.ACTION_COMPLETED,
            payload={"result": result.to_dict()},
        )
        await self._journal.append_event(thread_id, completed)
        yield completed

        content = json.dumps(
            result.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        message = Message(
            role="tool",
            name=call.name,
            tool_call_id=call.id,
            content=content,
        )
        await self._journal.append_message(thread_id, message)
        added = self._journal.message_added(message)
        await self._journal.append_event(thread_id, added)
        yield added

    async def _pause_task(
        self,
        thread_id: str,
        task: TaskRecord,
        supervisor: TaskSupervisor,
        reason: str,
    ) -> None:
        await self._journal.record_task_active_seconds(
            task.id, supervisor.checkpoint_active_seconds()
        )
        paused = await self._journal.transition_task(task.id, TaskStatus.PAUSED, reason)
        await self._journal.create_checkpoint(thread_id, "task-paused", {"task_id": paused.id, "status": paused.status.value, "reason": reason})

    def _advertised_tools(self) -> tuple[tuple[ToolDefinition, ...], set[str]]:
        try:
            tools = tuple(self._actions.tools())
            if not all(isinstance(tool, ToolDefinition) for tool in tools):
                raise TypeError("action dispatcher exposed an invalid tool")
            tool_names = {tool.name for tool in tools}
            if len(tool_names) != len(tools):
                raise ModelStreamError("action dispatcher exposed duplicate tools")
            return tools, tool_names
        except ModelStreamError:
            raise
        except Exception:
            raise ModelStreamError("action dispatcher exposed invalid tools") from None

    def _accumulate_model_event(
        self,
        event: ModelEvent,
        text_parts: list[str],
        calls: list[ToolCall],
    ) -> None:
        if not isinstance(event, ModelEvent):
            raise ModelStreamError("model emitted an invalid event")
        if event.kind is ModelEventKind.TEXT_DELTA:
            text_parts.append(event.text or "")
            if sum(map(len, text_parts)) > self._limits.max_assistant_chars:
                raise EngineLimitError("assistant output budget exceeded")
        elif event.kind is ModelEventKind.TOOL_CALL:
            if event.tool_call is None:
                raise ModelStreamError("tool call event has no call")
            calls.append(event.tool_call)


def _validation_fingerprint(request: ActionRequest, result: object) -> str | None:
    output = getattr(result, "output", {})
    if not isinstance(output, Mapping):
        return None
    if not getattr(result, "is_error", True) and output.get("returncode") in {0, None}:
        return None
    command = request.arguments.get("command")
    if not isinstance(command, str):
        return None
    prefix = " ".join(str(output.get(key, ""))[:256] for key in ("stdout", "stderr"))
    return f"{command[:120]}|{output.get('returncode')}|{output.get('reason', 'failed')}|{prefix[:256]}"


def _requires_decision(result: object) -> bool:
    output = getattr(result, "output", None)
    return (
        bool(getattr(result, "is_error", False))
        and isinstance(output, Mapping)
        and output.get("error") == "approval required in TUI"
    )
