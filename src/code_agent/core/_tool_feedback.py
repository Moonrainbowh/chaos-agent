from __future__ import annotations

from typing import Optional

from .models import ActionResult, ToolCall


def tool_failure(
    call: ToolCall,
    message: str,
    error_type: Optional[str] = None,
) -> ActionResult:
    output: dict[str, str] = {"error": message}
    if error_type is not None:
        output["type"] = error_type
    return ActionResult(
        request_id=call.id,
        name=call.name,
        output=output,
        is_error=True,
    )
