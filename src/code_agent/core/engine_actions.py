from __future__ import annotations

import json
from collections.abc import Mapping
from typing import AsyncIterator

from ._tool_feedback import tool_failure
from .action_execution import ActionExecutionContext, ActionLineage
from .cancellation import CancellationError, CancellationToken
from .errors import EngineLimitError, ModelStreamError
from .events import AgentEvent, EventKind
from .models import ActionRequest, ActionResult, Message, ModelEvent, ModelEventKind, ToolCall, ToolDefinition
from .task import TaskRecord, TaskStatus
from .task_state import TaskState
from .task_supervisor import SupervisionKind, TaskSupervisor
from .limits import TaskBudget


class AgentEngineActionMixin:
    """Internal action dispatch helpers separated from the model turn loop."""

    async def _persist_assistant_message(
        self, thread_id: str, text_parts: list[str], calls: list[ToolCall]
    ) -> tuple[Message, AgentEvent]:
        assistant = Message(role="assistant", content="".join(text_parts), tool_calls=tuple(calls))
        await self._journal.append_message(thread_id, assistant)
        event = self._journal.message_added(assistant)
        await self._journal.append_event(thread_id, event)
        return assistant, event

    async def _dispatch(self, thread_id: str, call: ToolCall, token: CancellationToken, *, is_available: bool, task: TaskRecord | None = None, supervisor: TaskSupervisor | None = None) -> AsyncIterator[AgentEvent]:
        request = ActionRequest(id=call.id, name=call.name, arguments=call.arguments)
        requested = AgentEvent(EventKind.ACTION_REQUESTED, {"request": request.to_dict()})
        await self._journal.append_event(thread_id, requested)
        yield requested
        if not is_available:
            result = tool_failure(call, "tool is not available")
        else:
            if supervisor is not None and call.name in {"write_file", "replace_text", "run_command", "run_verification"}:
                decision = supervisor.before_external_action()
                if decision.kind is SupervisionKind.PAUSE:
                    await self._pause_task(thread_id, task, supervisor, decision.reason or "task paused")
                    paused = AgentEvent(EventKind.TASK_PAUSED, {"task_id": task.id, "status": "paused", "reason": decision.reason or "task paused"})
                    await self._journal.append_event(thread_id, paused)
                    yield paused
                    return
                await self._journal.record_task_active_seconds(task.id, supervisor.checkpoint_active_seconds())
            started = AgentEvent(EventKind.ACTION_STARTED, {"request_id": call.id, "name": call.name})
            await self._journal.append_event(thread_id, started)
            yield started
            execution_context = self._action_execution_context(
                thread_id, call.id, task
            )
            result = await self._invoke_action(
                request, call, token, task, execution_context
            )
        if task is not None and _requires_decision(result):
            waiting = await self._journal.transition_task(task.id, TaskStatus.WAITING_DECISION, "approval required")
            event = AgentEvent(EventKind.TASK_DECISION_REQUIRED, {"task_id": waiting.id, "status": waiting.status.value})
            await self._journal.append_event(thread_id, event)
            yield event
            return
        paused = await self._record_action_state(
            thread_id, call, request, result, task, supervisor
        )
        if paused is not None:
            yield paused
        completed = AgentEvent(EventKind.ACTION_COMPLETED, {"result": result.to_dict()})
        await self._journal.append_event(thread_id, completed)
        yield completed
        message = Message(role="tool", name=call.name, tool_call_id=call.id, content=json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        await self._journal.append_message(thread_id, message)
        added = self._journal.message_added(message)
        await self._journal.append_event(thread_id, added)
        yield added

    def _action_execution_context(
        self,
        thread_id: str,
        request_id: str,
        task: TaskRecord | None,
    ) -> ActionExecutionContext:
        lineage: ActionLineage | None = self._action_lineage
        return ActionExecutionContext(
            owner_thread_id=lineage.owner_thread_id if lineage else thread_id,
            origin_thread_id=thread_id,
            request_id=request_id,
            task_id=lineage.task_id if lineage else task.id if task else None,
            parent_request_id=lineage.parent_request_id if lineage else None,
        )

    async def _invoke_action(
        self,
        request: ActionRequest,
        call: ToolCall,
        token: CancellationToken,
        task: TaskRecord | None,
        execution_context: ActionExecutionContext,
    ) -> ActionResult:
        try:
            if task is not None:
                result = await self._actions.dispatch(
                    request,
                    token,
                    task.contract.authorization,
                    execution_context=execution_context,
                )
            else:
                result = await self._actions.dispatch(
                    request,
                    token,
                    execution_context=execution_context,
                )
            if result.request_id != call.id or result.name != call.name:
                return tool_failure(call, "invalid tool result")
            return result
        except CancellationError:
            raise
        except Exception as exc:
            return tool_failure(
                call, "tool execution failed", type(exc).__name__
            )

    async def _record_action_state(
        self,
        thread_id: str,
        call: ToolCall,
        request: ActionRequest,
        result: ActionResult,
        task: TaskRecord | None,
        supervisor: TaskSupervisor | None,
    ) -> AgentEvent | None:
        if call.name not in {"read_file", "list_files", "search_text", "write_file", "replace_text", "run_command", "run_verification"}:
            return None
        state = await self._journal.reduce_task_state(thread_id, request, result)
        verification = getattr(self, "_verification", None)
        if task is not None and verification is not None:
            state = await verification.record_action(task, request, result, state)
            await self._journal.save_task_state(thread_id, state)
        if task is not None and supervisor is not None and call.name == "run_verification":
            return await self._record_validation_state(
                thread_id, request, result, task, supervisor, state
            )
        return None

    async def _record_validation_state(
        self,
        thread_id: str,
        request: ActionRequest,
        result: ActionResult,
        task: TaskRecord,
        supervisor: TaskSupervisor,
        state: TaskState,
    ) -> AgentEvent | None:
        fingerprint = _validation_fingerprint(request, result)
        changed_files = len(state.files_changed)
        decision = supervisor.observe_validation(fingerprint, changed_files)
        await self._journal.observe_task_validation(task.id, fingerprint, changed_files)
        await self._journal.create_checkpoint(thread_id, "validation-complete", {"task_id": task.id, "failed": fingerprint is not None})
        if decision.kind is not SupervisionKind.PAUSE:
            return None
        await self._pause_task(thread_id, task, supervisor, decision.reason or "validation paused")
        paused = AgentEvent(EventKind.TASK_PAUSED, {"task_id": task.id, "status": "paused", "reason": decision.reason or "validation paused"})
        await self._journal.append_event(thread_id, paused)
        return paused

    async def _run_suggested_verification(
        self,
        thread_id: str,
        task: TaskRecord,
        token: CancellationToken,
        supervisor: TaskSupervisor | None,
        budget: TaskBudget,
        tool_names: set[str],
    ) -> tuple[TaskBudget, tuple[AgentEvent, ...]] | None:
        verification = getattr(self, "_verification", None)
        if verification is None:
            return None
        suggestion = await verification.suggest_verification(
            task, await self._journal.load_task_state(thread_id)
        )
        if suggestion is None:
            return None
        reserved = await self._journal.reserve_task_budget(thread_id, tool_calls=1)
        if reserved is None:
            raise EngineLimitError("tool call budget exceeded")
        _, declared = await self._persist_assistant_message(thread_id, [], [suggestion])
        events = (declared,) + tuple(
            [event async for event in self._dispatch(
                thread_id, suggestion, token, is_available=suggestion.name in tool_names,
                task=task, supervisor=supervisor,
            )]
        )
        return reserved, events

    @staticmethod
    def _should_stop_after_action(events: tuple[AgentEvent, ...]) -> bool:
        return any(event.kind in {EventKind.TASK_PAUSED, EventKind.TASK_DECISION_REQUIRED} for event in events)

    async def _pause_task(self, thread_id: str, task: TaskRecord, supervisor: TaskSupervisor, reason: str) -> None:
        await self._journal.record_task_active_seconds(task.id, supervisor.checkpoint_active_seconds())
        paused = await self._journal.transition_task(task.id, TaskStatus.PAUSED, reason)
        await self._journal.create_checkpoint(thread_id, "task-paused", {"task_id": paused.id, "status": paused.status.value, "reason": reason})

    def _advertised_tools(self) -> tuple[tuple[ToolDefinition, ...], set[str]]:
        try:
            tools = tuple(self._actions.tools())
            if not all(isinstance(tool, ToolDefinition) for tool in tools):
                raise TypeError("action dispatcher exposed an invalid tool")
            names = {tool.name for tool in tools}
            if len(names) != len(tools):
                raise ModelStreamError("action dispatcher exposed duplicate tools")
            return tools, names
        except ModelStreamError:
            raise
        except Exception:
            raise ModelStreamError("action dispatcher exposed invalid tools") from None

    def _accumulate_model_event(self, event: ModelEvent, text_parts: list[str], calls: list[ToolCall]) -> None:
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
    if not isinstance(output, Mapping) or (not getattr(result, "is_error", True) and output.get("returncode") in {0, None}):
        return None
    if request.name == "run_verification":
        kind = request.arguments.get("kind")
        if not isinstance(kind, str):
            return None
        subject = "|".join((kind, str(request.arguments.get("cwd", ".")), repr(request.arguments.get("targets", ()))))
    else:
        subject = request.arguments.get("command")
    if not isinstance(subject, str):
        return None
    prefix = " ".join(str(output.get(key, ""))[:256] for key in ("stdout", "stderr"))
    return f"{subject[:120]}|{output.get('returncode')}|{output.get('reason', 'failed')}|{prefix[:256]}"


def _requires_decision(result: object) -> bool:
    output = getattr(result, "output", None)
    return bool(getattr(result, "is_error", False)) and isinstance(output, Mapping) and output.get("error") == "approval required in TUI"
