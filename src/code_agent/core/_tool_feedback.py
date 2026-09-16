from __future__ import annotations

from typing import Optional

from .models import ActionResult, ToolCall


def tool_failure(
    call: ToolCall,
    message: str,
    error_type: Optional[str] = None,
    error_code: Optional[str] = None,
) -> ActionResult:
    output: dict[str, str] = {"error": message}
    if error_type is not None:
        output["type"] = error_type
    if error_code is not None:
        output["error_code"] = error_code
    return ActionResult(
        request_id=call.id,
        name=call.name,
        output=output,
        is_error=True,
    )
