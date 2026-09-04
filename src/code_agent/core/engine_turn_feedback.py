from __future__ import annotations

import json
from collections.abc import Mapping

from ._tool_feedback import tool_failure
from .events import AgentEvent, EventKind
from .models import ActionResult, ToolCall


def circuit_breaker_result(
    action_history: list[str], call: ToolCall
) -> ActionResult | None:
    signature = (
        f"{call.name}:{json.dumps(dict(call.arguments), sort_keys=True)}"
    )
    action_history.append(signature)
    count = action_history.count(signature)
    if count < 3:
        return None
    return tool_failure(
        call,
        f"Action circuit breaker triggered: {call.name} with identical "
        f"arguments was called {count} times. Do not repeat this action; "
        "proceed with your analysis or response.",
    )


def is_in_flight_failure(event: AgentEvent) -> bool:
    if event.kind is not EventKind.ACTION_COMPLETED:
        return False
    result = event.payload.get("result")
    output = result.get("output") if isinstance(result, Mapping) else None
    return (
        isinstance(output, Mapping)
        and output.get("error_code") == "in_flight_validation_failed"
    )


def verification_failed(events: tuple[AgentEvent, ...]) -> bool:
    return any(
        event.kind is EventKind.ACTION_COMPLETED
        and isinstance(event.payload.get("result"), Mapping)
        and event.payload["result"].get("is_error") is True
        for event in events
    )
