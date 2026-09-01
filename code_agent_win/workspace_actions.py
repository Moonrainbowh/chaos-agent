from __future__ import annotations

import asyncio

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest, ActionResult

from code_agent_win.action_support import (
    edit_plan,
    list_action_result,
    ok_result,
    read_action_result,
    text_argument,
    text_format_fields,
)
from code_agent_win.rewind_capture_support import is_external_plan


async def execute_workspace_action(
    host: object,
    request: ActionRequest,
    cancellation: CancellationToken,
    context: ActionExecutionContext | None,
) -> ActionResult | None:
    arguments = request.arguments
    planned = await host.edit_plan_actions.execute(request, context, cancellation)
    if planned is not None:
        return planned
    if request.name == "read_file":
        return await read_action_result(request, host.files)
    if request.name == "list_files":
        root = arguments.get("root")
        if root is not None and not isinstance(root, str):
            raise ValueError("root must be text")
        return await list_action_result(request, host.files, host.git, root)
    if request.name == "search_text":
        matches = await asyncio.to_thread(
            host.files.search,
            text_argument(arguments, "pattern"),
            bool(arguments.get("regex", False)),
            bool(arguments.get("case_sensitive", False)),
        )
        return ok_result(
            request, {"matches": [match.__dict__ for match in matches]}
        )
    if request.name in {"write_file", "replace_text"}:
        plan = await asyncio.to_thread(edit_plan, host.editor, request)
        if host.capture is not None and not is_external_plan(plan.relative_path):
            if context is None:
                raise TypeError("execution_context is required for capture")
            cancellation.raise_if_cancelled()
            await host.capture.apply_edit(context, request, plan)
        else:
            await asyncio.to_thread(host.editor.apply, plan)
        if host.invalidate_cache is not None:
            host.invalidate_cache((plan.relative_path,))
        cancellation.raise_if_cancelled()
        return ok_result(
            request,
            {"path": plan.relative_path, **text_format_fields(plan.text_format)},
            {"diff": plan.diff},
        )
    return None


__all__ = ["execute_workspace_action"]
