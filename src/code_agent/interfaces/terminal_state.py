from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Mapping, Optional

from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ActionResult, Message, ModelEvent, ModelEventKind
from code_agent.interfaces.history import RestoredThread
from code_agent.sessions.models import GoalStatus


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
        self.timeline: list[str] = []
        self.diff: Optional[str] = None

    def restore(self, history: RestoredThread) -> None:
        """Project persisted thread records into a terminal-safe view model."""
        self.thread_id = history.thread_id
        self.transcript = _transcript_lines(history.messages)
        self.timeline = _action_timeline(history.events)
        self.diff = None
        self.status = "idle"
        for event in history.events:
            self._capture_diff(event)
            self._update_status(event)
        self.summary = _summary_lines(history, self.status)

    def apply(self, event: AgentEvent) -> None:
        self.timeline.append(_timeline_line(event))
        if event.kind is EventKind.RUN_STARTED:
            thread_id = event.payload.get("thread_id")
            if isinstance(thread_id, str):
                self.thread_id = thread_id
            self._update_status(event)
        elif event.kind is EventKind.MODEL_EVENT:
            self._apply_model_event(event)
        elif event.kind is EventKind.ACTION_REQUESTED:
            self._capture_diff(event)
        else:
            self._update_status(event)

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
            if self.transcript and self.transcript[-1].startswith("assistant: "):
                self.transcript[-1] += model_event.text
            else:
                self.transcript.append("assistant: " + model_event.text)
        elif model_event.kind is ModelEventKind.REASONING_DELTA and model_event.text:
            self.transcript.append("reasoning: " + model_event.text)

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
