from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest, ActionResult
from code_agent.context.repo_paths import canonical_path_key
from code_agent.workspace.errors import CodeSliceStaleError
from code_agent.workspace.files import CodeSliceRequest

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
    if request.name == "read_code_slices":
        return await _read_code_slices(host, request)
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


async def _read_code_slices(host: object, request: ActionRequest) -> ActionResult:
    arguments = request.arguments
    generation = arguments.get("generation")
    raw_targets = arguments.get("targets")
    if isinstance(generation, bool) or not isinstance(generation, int):
        raise ValueError("generation must be an integer")
    if not isinstance(raw_targets, Sequence) or isinstance(raw_targets, (str, bytes)):
        raise ValueError("targets must be an array")
    repo_index = getattr(host, "repo_index", None)
    if repo_index is None:
        return _stale_result(request, "repository index is unavailable")
    snapshot = await asyncio.to_thread(repo_index.snapshot_for_turn)
    if snapshot.generation != generation:
        return _stale_result(request, "repository generation changed")
    requests = tuple(_slice_request(item) for item in raw_targets)
    indexed = {
        canonical_path_key(entry.path): entry for entry in snapshot.entries
    }
    for target in requests:
        entry = indexed.get(canonical_path_key(target.path))
        signature = None if entry is None else entry.signature
        if signature is None or (
            signature.size_bytes,
            signature.modified_ns,
            signature.device_id,
            signature.file_id,
        ) != (
            target.expected_size_bytes,
            target.expected_modified_ns,
            target.expected_device_id,
            target.expected_file_id,
        ):
            return _stale_result(request, f"indexed signature changed: {target.path}")
    try:
        slices = await asyncio.to_thread(host.files.read_code_slices, requests)
    except CodeSliceStaleError as error:
        return _stale_result(request, str(error))
    refreshed = await asyncio.to_thread(repo_index.snapshot_for_turn)
    if refreshed.generation != generation:
        return _stale_result(request, "repository generation changed during read")
    return ok_result(request, {
        "generation": generation,
        "slices": [{
            "path": item.path,
            "start_line": item.start_line,
            "end_line": item.end_line,
            "total_lines": item.total_lines,
            "text": item.text,
            **text_format_fields(item.text_format),
        } for item in slices],
    })


def _slice_request(value: object) -> CodeSliceRequest:
    if not isinstance(value, Mapping):
        raise ValueError("each target must be an object")
    return CodeSliceRequest(
        value.get("path"),
        value.get("start_line"),
        value.get("end_line"),
        value.get("expected_size_bytes"),
        value.get("expected_modified_ns"),
        value.get("expected_device_id"),
        value.get("expected_file_id"),
    )  # type: ignore[arg-type]


def _stale_result(request: ActionRequest, detail: str) -> ActionResult:
    return ActionResult(
        request.id,
        request.name,
        {
            "error": "repository context is stale",
            "detail": detail,
            "error_code": "stale_repo_context",
        },
        is_error=True,
    )


__all__ = ["execute_workspace_action"]
