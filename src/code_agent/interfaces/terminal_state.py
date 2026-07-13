from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Mapping, Optional

from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ActionResult, Message, ModelEvent, ModelEventKind
from code_agent.interfaces.history import RestoredThread
from code_agent.sessions.models import GoalStatus
from code_agent.interfaces.terminal_display import DisplayKind, DisplayEntry, text_entry


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    name: str
    arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request_id must be non-blank text")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be non-blank text")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments must be a mapping")


class ApprovalBroker:
    """Bridge a policy dispatcher and an interactive approval surface."""

    def __init__(self) -> None:
        self._requests: asyncio.Queue[ApprovalRequest] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future[bool]] = {}

    async def request(
        self, request: ApprovalRequest, cancellation: CancellationToken
    ) -> bool:
        if not isinstance(cancellation, CancellationToken):
            raise TypeError("cancellation must be a CancellationToken")
        cancellation.raise_if_cancelled()
        if request.request_id in self._pending:
            raise ValueError("approval request id is already pending")
        decision: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[request.request_id] = decision
        await self._requests.put(request)
        cancelled = asyncio.create_task(cancellation.wait_async())
        try:
            done, _ = await asyncio.wait(
                (decision, cancelled),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancelled in done:
                cancellation.raise_if_cancelled()
            return decision.result()
        finally:
            self._pending.pop(request.request_id, None)
            cancelled.cancel()

    async def next_request(self) -> ApprovalRequest:
        while True:
            request = await self._requests.get()
            if request.request_id in self._pending:
                return request

    def resolve(self, request_id: str, approved: bool) -> bool:
        if not isinstance(request_id, str) or not isinstance(approved, bool):
            raise TypeError("request_id and approved must be text and bool")
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True


class TerminalState:
    """A dependency-free view model used by the Windows terminal renderer."""

    def __init__(self) -> None:
        self.thread_id: Optional[str] = None
        self.status = "idle"
        self.summary: list[str] = []
        self.transcript: list[str] = []
        self.entries: list[DisplayEntry] = []
        self._answer_parts: list[str] = []
        self._actions: list[str] = []
        self._failed_actions: list[str] = []
        self.active_action: str | None = None
        self.execution_summary = ""
        self.timeline: list[str] = []
        self.diff: Optional[str] = None
        self.task_id: Optional[str] = None
        self.task_status: Optional[str] = None
        self.task_budget_line: Optional[str] = None
        self.pending_decision: Optional[str] = None

    def restore(self, history: RestoredThread) -> None:
        """Project persisted thread records into a terminal-safe view model."""
        self.thread_id = history.thread_id
        self.transcript = _transcript_lines(history.messages)
        self.entries = [text_entry(DisplayKind.USER if line.startswith("user:") else DisplayKind.AGENT, line.split(": ", 1)[-1]) for line in self.transcript]
        self._answer_parts = []
        self._actions = []
        self._failed_actions = []
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
        self._answer_parts = []
        self._actions = []
        self._failed_actions = []
        self.active_action = None
        self.execution_summary = ""

    def apply(self, event: AgentEvent) -> None:
        self.timeline.append(_timeline_line(event))
        if event.kind is EventKind.RUN_STARTED:
            thread_id = event.payload.get("thread_id")
            if isinstance(thread_id, str):
                self.thread_id = thread_id
            self.begin_run()
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
            reason = event.payload.get("reason")
            self.pending_decision = reason if isinstance(reason, str) else "decision required"
        elif event.kind is EventKind.MODEL_EVENT:
            self._apply_model_event(event)
        elif event.kind is EventKind.ACTION_REQUESTED:
            self._capture_diff(event)
            self._answer_parts = []
            self.active_action = _action_name(event)
        elif event.kind is EventKind.ACTION_COMPLETED:
            line = _timeline_line(event)
            name = _action_name(event)
            self._actions.append(name)
            if line.startswith("failed "):
                self._failed_actions.append(name)
                self.entries.append(text_entry(DisplayKind.ERROR, name + " failed"))
            else:
                self.entries.append(text_entry(DisplayKind.SUCCESS, name + " completed"))
            self.active_action = None
        else:
            self._update_status(event)
        if event.kind is EventKind.COMPLETED:
            self._finish_display()

    def _update_status(self, event: AgentEvent) -> None:
        statuses = {
            EventKind.RUN_STARTED: "running",
            EventKind.CANCELLED: "cancelled",
            EventKind.ERROR: "error",
            EventKind.COMPLETED: "completed",
        }
        status = statuses.get(event.kind)
        if status is not None:
            self.status = status

    def _apply_model_event(self, event: AgentEvent) -> None:
        raw = event.payload.get("event")
        if not isinstance(raw, Mapping):
            return
        try:
            model_event = ModelEvent.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            return
        if model_event.kind is ModelEventKind.TEXT_DELTA and model_event.text:
            self._answer_parts.append(model_event.text)

    def _finish_display(self) -> None:
        answer = _compact_response("".join(self._answer_parts))
        if answer:
            self.transcript.append("assistant: " + answer)
            self.entries.append(text_entry(DisplayKind.AGENT, answer))
        if self._actions:
            self.execution_summary = _action_summary(self._actions, self._failed_actions)

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


def _action_summary(actions: list[str], failed: list[str]) -> str:
    return f"已完成 {len(actions)} 项操作" + (f" · {len(failed)} 项失败" if failed else "")


def _compact_response(value: str) -> str:
    lines, result = value.splitlines(), []
    for raw in lines:
        line = raw.rstrip()
        if line.strip() or (result and result[-1]): result.append(line)
    return "\n".join(result).strip()


def _transcript_lines(messages: tuple[Message, ...]) -> list[str]:
    lines: list[str] = []
    for message in messages:
        if message.role == "user":
            lines.append("user: " + message.content)
        elif message.role == "assistant" and message.content:
            lines.append("assistant: " + message.content)
    return lines


def _action_timeline(events: tuple[AgentEvent, ...]) -> list[str]:
    lines = [
        _timeline_line(event)
        for event in events
        if event.kind in {EventKind.ACTION_REQUESTED, EventKind.ACTION_COMPLETED}
    ]
    return lines[-8:]


def _summary_lines(history: RestoredThread, status: str) -> list[str]:
    active_goal = next(
        (goal.objective for goal in history.goals if goal.status is GoalStatus.ACTIVE),
        None,
    )
    fallback_goal = next(
        (message.content for message in history.messages if message.role == "user"),
        None,
    )
    summary: list[str] = []
    objective = active_goal if active_goal is not None else fallback_goal
    if objective is not None:
        summary.append("goal: " + _visible_text(objective))
    summary.append("status: " + status)
    if history.checkpoints:
        summary.append("checkpoint: " + history.checkpoints[-1].label)
    return summary


def _visible_text(value: str) -> str:
    return " ".join(value.split())[:120]
