from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.task_result import ResultCollector, TaskResult


MOBILE_EVENT_NAMES = frozenset(
    {
        "session_ready", "user_message", "assistant_delta", "task_started",
        "task_status", "tool_started", "tool_finished", "task_completed",
        "task_failed", "task_stopped", "connection_state",
    }
)


def _text(value: object, limit: int = 512) -> str | None:
    if not isinstance(value, str):
        return None
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    return "".join(
        character for character in value if character in "\n\t" or ord(character) >= 0x20
    )[:limit]


@dataclass(frozen=True)
class MobileEvent:
    event: str
    task_id: str | None = None
    session_id: str | None = None
    sequence: int = 0
    data: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.event not in MOBILE_EVENT_NAMES:
            raise ValueError(f"unsupported mobile event: {self.event}")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"event": self.event, "sequence": self.sequence}
        if self.task_id is not None:
            value["task_id"] = self.task_id
        if self.session_id is not None:
            value["session_id"] = self.session_id
        if self.data:
            value["data"] = dict(self.data)
        return value


def event_from_agent(
    event: AgentEvent,
    *,
    task_id: str,
    session_id: str,
    sequence: int,
) -> MobileEvent | None:
    payload = event.payload
    common = {"task_id": task_id, "session_id": session_id, "sequence": sequence}
    if event.kind is EventKind.MODEL_EVENT:
        model_event = payload.get("event")
        if isinstance(model_event, Mapping) and model_event.get("kind") == "text_delta":
            text = _text(model_event.get("text"))
            if text:
                return MobileEvent("assistant_delta", data={"text": text}, **common)
        return None
    if event.kind is EventKind.TASK_STATUS_CHANGED:
        status = _text(payload.get("status"), 64) or "unknown"
        return MobileEvent("task_status", data={"status": status}, **common)
    if event.kind is EventKind.ACTION_STARTED:
        data = {key: value for key in ("name", "request_id") if (value := _text(payload.get(key), 128))}
        return MobileEvent("tool_started", data=data, **common)
    if event.kind is EventKind.ACTION_COMPLETED:
        result = payload.get("result")
        data: dict[str, Any] = {}
        if isinstance(result, Mapping):
            for key in ("name", "request_id", "is_error"):
                value = result.get(key)
                if isinstance(value, (str, bool, int)):
                    data[key] = _text(value, 128) if isinstance(value, str) else value
        return MobileEvent("tool_finished", data=data, **common)
    if event.kind is EventKind.COMPLETED:
        collected = ResultCollector()
        collected.observe(event)
        return MobileEvent("task_completed", data={'result': collected.result.to_dict()}, **common)
    if event.kind is EventKind.TASK_RESULT:
        result = TaskResult.from_dict(payload['result'])
        return MobileEvent('task_status', data={'status': result.execution_status, 'result': result.to_dict()}, **common)
    if event.kind in {EventKind.TASK_DECISION_REQUIRED, EventKind.TASK_PAUSED}:
        status = 'waiting_decision' if event.kind is EventKind.TASK_DECISION_REQUIRED else 'paused'
        return MobileEvent('task_status', data={'status': status}, **common)
    if event.kind is EventKind.CANCELLED:
        reason = _text(payload.get("reason"), 128)
        return MobileEvent("task_stopped", data={"reason": reason} if reason else None, **common)
    if event.kind is EventKind.ERROR:
        data = {key: value for key in ("code", "error_type") if (value := _text(payload.get(key), 128))}
        return MobileEvent("task_status", data={'status': 'interrupted', **data}, **common)
    return None
