from __future__ import annotations

import json
from collections.abc import Mapping

from ._tool_feedback import tool_failure
from .events import AgentEvent, EventKind
from .models import ActionResult, ToolCall
from .exploration_repeat import READ_ONLY_TOOLS
from ._json import plain


def call_signature(call: ToolCall) -> str:
    return f"{call.name}:{json.dumps(plain(call.arguments), sort_keys=True)}"


def format_action_error(result_dict: Mapping[str, object]) -> str:
    output = result_dict.get("output")
    if isinstance(output, Mapping):
        detail = output.get("detail")
        error = output.get("error")
        if detail and error:
            return f"{error}: {detail}"
        if detail:
            return str(detail)
        if error:
            return str(error)
    return str(output) if output else "unknown error"


DIAGNOSTIC_REFLECTION_SCAFFOLD = (
    "\n\n[Diagnostic Reflection / 诊断反思引导]:\n"
    "1. Root Cause: Analyze why the previous attempt failed based on the error above.\n"
    "2. Assumption Check: What assumption about the workspace, arguments, or environment proved incorrect?\n"
    "3. Alternative Path: Formulate a distinct alternative approach or verify facts before taking further modifying actions."
)


def build_diagnostic_reflection(error_message: str) -> str:
    """Wrap an action or verification failure in a structured reflection scaffold."""
    return f"{error_message.strip()}{DIAGNOSTIC_REFLECTION_SCAFFOLD}"


def duplicate_failed_call_result(
    last_failed_call: tuple[str, str] | None, call: ToolCall
) -> ActionResult | None:
    if last_failed_call is None:
        return None
    prev_sig, prev_error = last_failed_call
    if call_signature(call) != prev_sig:
        return None
    msg = (
        "The identical tool call failed on the previous attempt.\n\n"
        f"Previous error:\n{prev_error}\n\n"
        "Do not repeat the same call unchanged. Modify the arguments before retrying."
    )
    return tool_failure(
        call,
        build_diagnostic_reflection(msg),
        error_code="duplicate_failed_call_blocked",
    )


def circuit_breaker_result(
    action_history: list[str], call: ToolCall
) -> ActionResult | None:
    signature = (
        f"{call.name}:{json.dumps(plain(call.arguments), sort_keys=True)}"
    )
    signature = call_signature(call)
    if call.name in READ_ONLY_TOOLS:
        return None
    action_history.append(signature)
    count = action_history.count(signature)
    if count < 3:
        return None
    return tool_failure(
        call,
        f"Action circuit breaker triggered: {call.name} with identical "
        f"arguments was called {count} times. Do not repeat this action; "
        "proceed with your analysis or response.",
        error_code="repeated_action_blocked",
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


def is_validation_failure(result: object) -> bool:
    if isinstance(result, ActionResult):
        if not result.is_error:
            return False
        output = result.output
    elif isinstance(result, Mapping):
        if result.get("is_error") is not True:
            return False
        output = result.get("output")
    else:
        return False

    if isinstance(output, Mapping):
        code = output.get("error_code")
        if code in {"invalid_tool_arguments", "duplicate_failed_call_blocked", "summary_tool_call_rejected"}:
            return True
        error_msg = output.get("error")
        if error_msg in {"invalid tool arguments", "shell syntax mismatch", "process contract mismatch"}:
            return True
    return False

