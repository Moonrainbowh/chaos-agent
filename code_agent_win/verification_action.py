from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest, ActionResult
from code_agent.runtime.models import CommandSpec
from code_agent.verification.models import (
    VerificationKind,
    VerificationRequest,
    VerificationUnavailable,
)

from code_agent_win.action_support import error_result, text_argument
from code_agent_win.rewind_capture_support import record_unknown_gap
from code_agent_win.tool_support import command_action_result


async def run_verification_action(
    request: ActionRequest,
    runtime: object | None,
    verification: object | None,
    capture: object | None,
    context: ActionExecutionContext | None,
    cancellation: CancellationToken,
    gap_recorded: bool,
    invalidate_cache: Callable[[Sequence[str]], None] | None,
) -> ActionResult:
    if runtime is None or verification is None:
        return error_result(request, "verification unavailable")
    arguments = request.arguments
    command = verification.build(
        VerificationRequest(
            VerificationKind(text_argument(arguments, "kind")),
            cwd=str(arguments.get("cwd", ".")),
            targets=tuple(arguments.get("targets", ())),
            timeout_s=int(arguments.get("timeout_s", 300)),
        )
    )
    if isinstance(command, VerificationUnavailable):
        return error_result(request, "verification unavailable", command.reason)
    if not gap_recorded:
        await record_unknown_gap(capture, context, request, cancellation)
    try:
        result = await runtime.run(
            CommandSpec(
                cwd=Path(command.cwd),
                argv=command.argv,
                timeout_s=command.timeout_s,
            ),
            cancellation,
            None,
        )
    finally:
        if invalidate_cache is not None:
            invalidate_cache(())
    action_result = command_action_result(request, result)
    return ActionResult(
        action_result.request_id,
        action_result.name,
        {**action_result.output, "kind": text_argument(arguments, "kind")},
        action_result.is_error,
        action_result.metadata,
    )


__all__ = ["run_verification_action"]
