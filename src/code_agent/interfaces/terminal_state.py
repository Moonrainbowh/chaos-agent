from __future__ import annotations
from .terminal_history_summary import _summary_lines
from .terminal_context_budget import ContextBudgetDisplay

from typing import Mapping, Optional

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ActionResult, Message, ModelEvent, ModelEventKind
from code_agent.interfaces.history import RestoredThread
from code_agent.sessions.models import GoalStatus
from code_agent.interfaces.terminal_display import DisplayKind, DisplayEntry, text_entry
from code_agent.interfaces.action_summary import action_activity, action_summary
from code_agent.interfaces.approval import ApprovalBroker, ApprovalRequest
from code_agent.interfaces.token_rate import TokenRateTracker
from code_agent.interfaces.streaming_state import DraftBuffer
from .terminal_state_text import compact_response as _compact_response, transcript_lines as _transcript_lines, action_summary as _action_summary


class TerminalState:
    """A dependency-free view model used by the Windows terminal renderer."""

    def __init__(self) -> None:
        self.thread_id: Optional[str] = None
        self.status = "idle"
        self.summary: list[str] = []
        self.transcript: list[str] = []
        self.entries: list[DisplayEntry] = []
        self._draft = DraftBuffer()
        self._actions: list[str] = []
        self._failed_actions: list[str] = []
        self._action_requests: dict[str, Mapping[str, object]] = {}
        self.active_action: str | None = None
        self.execution_summary = ""
        self.timeline: list[str] = []
        self.diff: Optional[str] = None
        self.task_id: Optional[str] = None
        self.task_status: Optional[str] = None
        self.task_budget_line: Optional[str] = None
        self.pending_decision: Optional[str] = None
        self.token_rate = TokenRateTracker()
        self.total_tokens: int = 0
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.last_rate: float | None = None
        self.context_budget = ContextBudgetDisplay()

    @property
    def draft_answer(self) -> str:
        return self._draft.safe_text
    @property
    def has_draft(self) -> bool:
        return self._draft.has_text
    @property
    def draft_revision(self) -> int:
        return self._draft.revision

    def restore(self, history: RestoredThread) -> None:
        """Project persisted thread records into a terminal-safe view model."""
        self.context_budget = ContextBudgetDisplay()
        self.thread_id = history.thread_id
        self.transcript = _transcript_lines(history.messages)
        self.entries = [text_entry(DisplayKind.USER if line.startswith("user:") else DisplayKind.AGENT, line.split(": ", 1)[-1]) for line in self.transcript]
        self._draft.clear()
        self._actions = []
        self._failed_actions = []
        self._action_requests = {}
        self.active_action = None
        self.execution_summary = ""
        self.timeline = _action_timeline(history.events)
        self.diff = None
        self.status = "idle"
        for event in history.events:
            self._capture_diff(event)
            self._update_status(event)
        self.summary = _summary_lines(history, self.status)

    def begin_run(self) -> None:
        """Reset transient progress so a new prompt cannot inherit the prior result."""
        self.status = "running"
        self.pending_decision = None
        self._draft.clear()
        self._actions = []
        self._failed_actions = []
        self._action_requests = {}
        self.active_action = None
        self.execution_summary = ""
        self.token_rate.reset()

    def apply(self, event: AgentEvent) -> None:
        self.timeline.append(_timeline_line(event))
        if event.kind in {EventKind.CANCELLED, EventKind.ERROR}:
            self._freeze_partial_answer()
        if event.kind is EventKind.RUN_STARTED:
            thread_id = event.payload.get("thread_id")
            if isinstance(thread_id, str):
                self.thread_id = thread_id
            self.begin_run()
        elif event.kind in {
            EventKind.TURN_STARTED,
            EventKind.CONTEXT_BUILT,
        }:
            self._update_status(event)
        elif event.kind is EventKind.MODEL_STARTED:
            self._draft.clear(); self.token_rate.reset()
            self._update_status(event)
        elif event.kind in {EventKind.TASK_CREATED, EventKind.TASK_STATUS_CHANGED, EventKind.TASK_PAUSED}:
            task_id = event.payload.get("task_id")
            status = event.payload.get("status")
            if isinstance(task_id, str): self.task_id = task_id
            if isinstance(status, str): self.task_status = status
            self.status = status if isinstance(status, str) else "task"
        elif event.kind is EventKind.TASK_BUDGET_WARNING:
            reason = event.payload.get("reason")
            self.task_budget_line = reason if isinstance(reason, str) else "budget warning"
        elif event.kind is EventKind.TASK_DECISION_REQUIRED:
            self._apply_decision(event)
        elif event.kind is EventKind.MODEL_EVENT:
            self._apply_model_event(event)
        elif event.kind is EventKind.MESSAGE_ADDED:
            self._apply_completed_message(event)
        elif event.kind is EventKind.ACTION_REQUESTED:
            self._apply_action_request(event)
        elif event.kind is EventKind.ACTION_COMPLETED:
            line = _timeline_line(event)
            name = _action_name(event)
            result = _action_result(event)
            self._actions.append(name)
            if line.startswith("failed "):
                self._failed_actions.append(name)
                self.entries.append(text_entry(DisplayKind.ERROR, action_summary(name, self._action_requests.pop(result.request_id, None) if result else None, result) if result else name + " failed"))
            else:
                self.entries.append(text_entry(DisplayKind.TOOL, action_summary(name, self._action_requests.pop(result.request_id, None) if result else None, result) if result else name + " completed"))
            self.active_action = None
        else:
            self._update_status(event)
        if event.kind is EventKind.COMPLETED:
            self._finish_display()

    def _apply_decision(self, event: AgentEvent) -> None:
        reason = event.payload.get("reason")
        self.pending_decision = reason if isinstance(reason, str) else "decision required"
        self.status = "waiting_decision"
        self.entries.append(text_entry(DisplayKind.WARNING, self.pending_decision + " · Continue with instructions, inspect /evidence, or use /accept for partial delivery."))

    def _apply_action_request(self, event: AgentEvent) -> None:
        self._capture_diff(event)
        self._draft.clear()
        self.status = "running"
        request = event.payload.get("request")
        arguments = request.get("arguments") if isinstance(request, Mapping) else None
        self.active_action = action_activity(_action_name(event), arguments)
        if isinstance(request, Mapping) and isinstance(request.get("id"), str):
            self._action_requests[request["id"]] = request

    def _update_status(self, event: AgentEvent) -> None:
        self.context_budget.apply(event)
        if event.kind in {EventKind.TASK_CREATED, EventKind.TASK_STATUS_CHANGED, EventKind.TASK_PAUSED}:
            self.task_id = event.payload.get("task_id", self.task_id)
            self.task_status = event.payload.get("status", self.task_status)
            if self.task_status:
                self.status = self.task_status
            return
        if event.kind is EventKind.CANCELLED:
            self.status = "paused" if event.payload.get("reason") == "user requested pause" else "cancelled"
            return
        statuses = {
            EventKind.RUN_STARTED: "running",
            EventKind.TURN_STARTED: "building_context",
            EventKind.CONTEXT_BUILT: "waiting_model",
            EventKind.MODEL_STARTED: "waiting_model",
            EventKind.ERROR: "error",
            EventKind.COMPLETED: "completed",
        }
        status = statuses.get(event.kind)
        if status is not None:
            self.status = status

    def _apply_model_event(self, event: AgentEvent) -> None:
        self.context_budget.apply(event)
        raw = event.payload.get("event")
        if not isinstance(raw, Mapping):
            return
        try:
            model_event = ModelEvent.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            return
        if model_event.kind is ModelEventKind.TEXT_DELTA and model_event.text:
            self.status = "streaming_response"
            self._draft.append(model_event.text)
            self.token_rate.observe_text(model_event.text)
        elif model_event.kind is ModelEventKind.REASONING_DELTA:
            self.status = "reasoning"
        elif model_event.kind is ModelEventKind.TOOL_CALL:
            self.status = "preparing_action"
            if model_event.tool_call is not None:
                self.active_action = action_activity(
                    model_event.tool_call.name, model_event.tool_call.arguments,
                )
        elif model_event.kind is ModelEventKind.USAGE and model_event.usage is not None:
            self.token_rate.calibrate(model_event.usage.output_tokens)
            self.input_tokens = model_event.usage.input_tokens
            self.output_tokens = model_event.usage.output_tokens
            self.total_tokens = model_event.usage.total_tokens

    def _apply_completed_message(self, event: AgentEvent) -> None:
        raw = event.payload.get("message")
        if not isinstance(raw, Mapping):
            return
        try:
            message = Message.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            return
        if message.role == "assistant" and message.content and not message.tool_calls:
            self._finish_display(message.content)

    def _finish_display(self, completed_text: str | None = None) -> None:
        answer = _compact_response(completed_text if completed_text is not None else self._draft.raw_text)
        self._draft.clear()
        if answer:
            self.transcript.append("assistant: " + answer)
            self.entries.append(text_entry(DisplayKind.AGENT, answer))
        if self._actions:
            self.execution_summary = _action_summary(self._actions, self._failed_actions)

    def _freeze_partial_answer(self) -> None:
        answer = _compact_response(self._draft.take_partial())
        if answer:
            self.entries.append(text_entry(DisplayKind.PARTIAL_AGENT, answer))

    def _capture_diff(self, event: AgentEvent) -> None:
        request = event.payload.get("request")
        if not isinstance(request, Mapping):
            return
        arguments = request.get("arguments")
        if not isinstance(arguments, Mapping):
            return
        diff = arguments.get("diff")
        if isinstance(diff, str):
            self.diff = diff

def _timeline_line(event: AgentEvent) -> str:
    if event.kind is EventKind.ACTION_REQUESTED:
        request = event.payload.get("request")
        if isinstance(request, Mapping) and isinstance(request.get("name"), str):
            return "requested " + request["name"]
    if event.kind is EventKind.ACTION_COMPLETED:
        result = event.payload.get("result")
        if isinstance(result, Mapping):
            try:
                action_result = ActionResult.from_dict(result)
            except (KeyError, TypeError, ValueError):
                return "completed action"
            if action_result.is_error:
                return "failed " + action_result.name
            return "completed " + action_result.name
    return event.kind.value.replace("_", " ")

def _action_name(event: AgentEvent) -> str:
    if event.kind is EventKind.ACTION_REQUESTED:
        request = event.payload.get("request")
        if isinstance(request, Mapping) and isinstance(request.get("name"), str): return request["name"]
    result = event.payload.get("result")
    if isinstance(result, Mapping) and isinstance(result.get("name"), str): return result["name"]
    return "action"
def _action_result(event: AgentEvent) -> ActionResult | None:
    result = event.payload.get("result")
    if not isinstance(result, Mapping):
        return None
    try:
        return ActionResult.from_dict(result)
    except (KeyError, TypeError, ValueError):
        return None


def _action_timeline(events: tuple[AgentEvent, ...]) -> list[str]:
    lines = [
        _timeline_line(event)
        for event in events
        if event.kind in {EventKind.ACTION_REQUESTED, EventKind.ACTION_COMPLETED}
    ]
    return lines[-8:]
