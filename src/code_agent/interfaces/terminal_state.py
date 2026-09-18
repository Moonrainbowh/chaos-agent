from __future__ import annotations
from .terminal_history_summary import _summary_lines
from .terminal_context_budget import ContextBudgetDisplay

from typing import Mapping, Optional
import re

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
        self.plan_text = ""
        self.plan_completed_steps = 0
        self._actions: list[str] = []
        self._failed_actions: list[str] = []
        self._action_requests: dict[str, Mapping[str, object]] = {}
        self.active_action: str | None = None
        self.execution_summary = ""
        self.timeline: list[str] = []
        self.diff: Optional[str] = None
        self.task_id: Optional[str] = None
        self.task_status: Optional[str] = None
        self.task_stop_reason: Optional[str] = None
        self.task_budget_line: Optional[str] = None
        self.phase_durations: dict[str, int] = {}
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
        self.plan_text = ""
        self.plan_completed_steps = 0
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

    def begin_run(self, *, preserve_plan: bool = False) -> None:
        """Reset transient progress so a new prompt cannot inherit the prior result."""
        self.status = "running"
        self.pending_decision = None
        if not preserve_plan:
            self.plan_text = ""
            self.plan_completed_steps = 0
        self._draft.clear()
        self._actions = []
        self._failed_actions = []
        self._action_requests = {}
        self.active_action = None
        self.execution_summary = ""
        self.token_rate.reset()
        self.phase_durations = {}

    def apply(self, event: AgentEvent) -> None:
        self.timeline.append(_timeline_line(event))
        if event.kind in {EventKind.CANCELLED, EventKind.ERROR}:
            self._freeze_partial_answer()
        if event.kind is EventKind.RUN_STARTED:
            thread_id = event.payload.get("thread_id")
            preserve_plan = bool(self.plan_text and thread_id == self.thread_id)
            if isinstance(thread_id, str):
                self.thread_id = thread_id
            self.begin_run(preserve_plan=preserve_plan)
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
            reason = event.payload.get("reason")
            if isinstance(reason, str):
                self.task_stop_reason = reason
            elif event.kind is EventKind.TASK_CREATED or self.task_status != "paused":
                self.task_stop_reason = None
            self.status = status if isinstance(status, str) else "task"
        elif event.kind is EventKind.TASK_BUDGET_WARNING:
            lease_line = _lease_budget_line(event.payload)
            if lease_line is not None:
                self.task_budget_line = lease_line
            elif event.payload.get("category") != "lease":
                reason = event.payload.get("reason")
                self.task_budget_line = reason if isinstance(reason, str) else "budget warning"
        elif event.kind is EventKind.PHASE_COMPLETED:
            self._apply_phase_timing(event)
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
            self.plan_completed_steps = min(
                self.plan_completed_steps + 1,
                max(0, len(self._plan_steps()) - 1),
            )
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
        # Keep the durable waiting-decision state, but do not add a verbose
        # yellow transcript block. The compact status line remains available
        # so the task can still be resumed or accepted explicitly.

    def _apply_action_request(self, event: AgentEvent) -> None:
        self._capture_diff(event)
        self._draft.clear()
        self.status = "running"
        request = event.payload.get("request")
        arguments = request.get("arguments") if isinstance(request, Mapping) else None
        self.active_action = action_activity(_action_name(event), arguments)
        if isinstance(request, Mapping) and isinstance(request.get("id"), str):
            self._action_requests[request["id"]] = request

    def _apply_phase_timing(self, event: AgentEvent) -> None:
        phase = event.payload.get("phase")
        duration_ms = event.payload.get("duration_ms")
        if (
            phase not in {"context", "model", "action"}
            or isinstance(duration_ms, bool)
            or not isinstance(duration_ms, int)
            or duration_ms < 0
        ):
            return
        current = self.phase_durations.get(phase, 0)
        self.phase_durations[phase] = min(86_400_000, current + duration_ms)

    def _plan_steps(self) -> list[str]:
        return [line.strip() for line in self.plan_text.splitlines() if line.strip()]

    def _update_status(self, event: AgentEvent) -> None:
        self.context_budget.apply(event)
        if event.kind in {EventKind.TASK_CREATED, EventKind.TASK_STATUS_CHANGED, EventKind.TASK_PAUSED}:
            self.task_id = event.payload.get("task_id", self.task_id)
            self.task_status = event.payload.get("status", self.task_status)
            reason = event.payload.get("reason")
            if isinstance(reason, str):
                self.task_stop_reason = reason
            elif event.kind is EventKind.TASK_CREATED or self.task_status != "paused":
                self.task_stop_reason = None
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
        if message.role == "assistant" and message.content:
            plans = list(re.finditer(r"<(plan|replan)>(.*?)</\1>", message.content, re.DOTALL))
            if plans:
                new_plan = plans[-1].group(2).strip()
                if new_plan != self.plan_text:
                    self.plan_text = new_plan
                    self.plan_completed_steps = 0
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


def _lease_budget_line(payload: Mapping[str, object]) -> str | None:
    if payload.get("category") != "lease":
        return None
    phase = payload.get("phase")
    tier = payload.get("tier")
    renewals = payload.get("renewals")
    reason = payload.get("reason")
    if (
        phase not in {"renewed", "converge"}
        or tier not in {"quick", "standard", "deep"}
        or isinstance(renewals, bool)
        or not isinstance(renewals, int)
        or renewals < 0
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        return None
    detail = " ".join(reason.split())[:120]
    if phase == "renewed":
        return f"Lease renewed to {tier} ({renewals}): {detail}"[:180]
    return f"Lease converging at {tier}: {detail}"[:180]
