from __future__ import annotations

from collections.abc import Mapping

from acp import (
    start_tool_call,
    update_agent_message_text,
    update_agent_thought_text,
    update_tool_call,
)

from code_agent.core._json import plain
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ActionRequest, ActionResult, ModelEvent, ModelEventKind


def acp_updates(event: AgentEvent) -> tuple[object, ...]:
    """Convert one durable Chaos event to zero or more ACP session updates."""
    if event.kind is EventKind.MODEL_EVENT:
        return _model_updates(event.payload.get("event"))
    if event.kind is EventKind.ACTION_REQUESTED:
        return _requested_update(event.payload.get("request"))
    if event.kind is EventKind.ACTION_STARTED:
        request_id = event.payload.get("request_id")
        if isinstance(request_id, str):
            return (update_tool_call(request_id, status="in_progress"),)
    if event.kind is EventKind.ACTION_COMPLETED:
        return _completed_update(event.payload.get("result"))
    if event.kind is EventKind.ERROR:
        code = event.payload.get("code", "agent_error")
        return (update_agent_message_text(f"Chaos Agent stopped: {code}"),)
    return ()


def acp_stop_reason(event: AgentEvent) -> str | None:
    """Map terminal Chaos events to ACP v1 prompt stop reasons."""
    if event.kind is EventKind.CANCELLED:
        return "cancelled"
    if event.kind is EventKind.ERROR:
        return "refusal"
    if event.kind is EventKind.COMPLETED:
        return "end_turn"
    return None


def _model_updates(value: object) -> tuple[object, ...]:
    if not isinstance(value, Mapping):
        return ()
    try:
        event = ModelEvent.from_dict(value)
    except (KeyError, TypeError, ValueError):
        return ()
    if event.kind is ModelEventKind.TEXT_DELTA and event.text:
        return (update_agent_message_text(event.text),)
    if event.kind is ModelEventKind.REASONING_DELTA and event.text:
        return (update_agent_thought_text(event.text),)
    return ()


def _requested_update(value: object) -> tuple[object, ...]:
    if not isinstance(value, Mapping):
        return ()
    try:
        request = ActionRequest.from_dict(value)
    except (KeyError, TypeError, ValueError):
        return ()
    return (
        start_tool_call(
            request.id,
            request.name,
            kind=_tool_kind(request.name),
            status="pending",
            raw_input=plain(request.arguments),
        ),
    )


def _completed_update(value: object) -> tuple[object, ...]:
    if not isinstance(value, Mapping):
        return ()
    try:
        result = ActionResult.from_dict(value)
    except (KeyError, TypeError, ValueError):
        return ()
    return (
        update_tool_call(
            result.request_id,
            status="failed" if result.is_error else "completed",
            raw_output=plain(result.output),
        ),
    )


def _tool_kind(name: str) -> str:
    lowered = name.casefold()
    if lowered.startswith(("read_", "git_diff")):
        return "read"
    if lowered.startswith(("search_", "list_", "git_status")):
        return "search"
    if lowered.startswith(("write_", "replace_", "apply_")):
        return "edit"
    if lowered.startswith(("run_", "terminal.")):
        return "execute"
    if lowered.startswith(("load_", "plan_")):
        return "think"
    if lowered.startswith(("mcp.", "plugin.")):
        return "fetch"
    return "other"


__all__ = ["acp_stop_reason", "acp_updates"]
