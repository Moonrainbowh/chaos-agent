from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Mapping

from code_agent.core.models import ActionRequest, ActionResult
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.errors import WindowsFileBusyError
from code_agent.workspace._text_codec import TextFileFormat
from code_agent.workspace.git import GitWorkspace
from code_agent_win.peer_tools import validate_peer_tool_arguments
from code_agent_win.tools import (
    powershell_compatibility_error,
    process_compatibility_error,
    validate_tool_arguments,
)


LIST_FILES_RESULT_LIMIT = 200
LIST_FILES_SCAN_LIMIT = 200_000


def preflight_action(
    request: ActionRequest, translated: ActionRequest
) -> ActionResult | None:
    peer_error = validate_peer_tool_arguments(
        translated.name, translated.arguments
    )
    if peer_error is not None:
        return error_result(request, "invalid tool arguments", peer_error)
    validation_error = validate_tool_arguments(
        translated.name, translated.arguments
    )
    if validation_error is not None:
        return error_result(request, "invalid tool arguments", validation_error)
    if translated.name == "run_command":
        mismatch = powershell_compatibility_error(
            text_argument(translated.arguments, "command")
        )
        if mismatch is not None:
            return error_result(request, "shell syntax mismatch", mismatch)
    if translated.name == "run_process_v1":
        mismatch = process_compatibility_error(translated.arguments)
        if mismatch is not None:
            return error_result(request, "process contract mismatch", mismatch)
    return None


def edit_plan(editor: WorkspaceEditor, request: ActionRequest) -> object:
    arguments = request.arguments
    encoding = optional_text_argument(arguments, "encoding", "auto")
    if request.name == "write_file":
        return editor.plan_write(
            text_argument(arguments, "path"),
            text_argument(arguments, "content"),
            encoding=encoding,
        )
    return editor.plan_replace(
        text_argument(arguments, "path"),
        text_argument(arguments, "old_text"),
        text_argument(arguments, "new_text"),
        encoding=encoding,
    )


def text_argument(arguments: Mapping[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def optional_text_argument(
    arguments: Mapping[str, object], name: str, default: str
) -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


async def read_action_result(
    request: ActionRequest, files: WorkspaceFiles
) -> ActionResult:
    arguments = request.arguments
    document = await asyncio.to_thread(
        files.read_text,
        text_argument(arguments, "path"),
        encoding=optional_text_argument(arguments, "encoding", "auto"),
    )
    return ok_result(
        request,
        {
            "path": document.relative_path,
            "text": document.text,
            "total_lines": document.total_lines,
            **text_format_fields(document.text_format),
        },
    )


def text_format_fields(text_format: TextFileFormat) -> dict[str, object]:
    if not isinstance(text_format, TextFileFormat):
        raise TypeError("text_format must be a TextFileFormat")
    fields: dict[str, object] = {
        "encoding": text_format.encoding,
        "bom": text_format.bom,
        "newline": text_format.newline,
    }
    if text_format.code_page is not None:
        fields["code_page"] = text_format.code_page
    return fields


def ok_result(
    request: ActionRequest,
    output: Mapping[str, object],
    metadata: Mapping[str, object] | None = None,
) -> ActionResult:
    return ActionResult(
        request.id, request.name, dict(output), metadata=metadata or {}
    )


def error_result(
    request: ActionRequest,
    message: str,
    detail: str | None = None,
    *,
    error_code: str | None = None,
) -> ActionResult:
    output = {"error": message}
    if detail is not None:
        output["detail"] = detail
    if error_code is not None:
        output["error_code"] = error_code
    return ActionResult(request.id, request.name, output, is_error=True)


def exception_result(request: ActionRequest, error: Exception) -> ActionResult:
    if isinstance(error, WindowsFileBusyError):
        return error_result(
            request,
            "Windows file operation blocked",
            str(error)[:1_024],
            error_code="file_busy",
        )
    return error_result(request, "action failed", type(error).__name__)


async def with_action_duration(result: Awaitable[ActionResult]) -> ActionResult:
    """Await an action and add monotonic execution duration when absent."""
    started_at = time.perf_counter()
    completed = await result
    metadata = dict(completed.metadata)
    metadata.setdefault(
        "duration_ms",
        max(0, round((time.perf_counter() - started_at) * 1000)),
    )
    return ActionResult(
        completed.request_id,
        completed.name,
        completed.output,
        completed.is_error,
        metadata,
    )


async def list_action_result(
    request: ActionRequest,
    files: WorkspaceFiles,
    git: GitWorkspace | None,
    root: str | None,
) -> ActionResult:
    """Use Git's fast inventory for the workspace root, then bound model output."""
    is_workspace_root = root is None or files.guard.resolve(root) == files.guard.root
    if git is not None and is_workspace_root:
        candidates = await asyncio.to_thread(git.snapshot_paths)
        listed = await asyncio.to_thread(
            files.list_known_files,
            candidates,
            max_entries=LIST_FILES_RESULT_LIMIT + 1,
            max_scanned_entries=LIST_FILES_SCAN_LIMIT,
        )
    else:
        listed = await asyncio.to_thread(
            files.list_files,
            root,
            max_entries=LIST_FILES_RESULT_LIMIT + 1,
            max_scanned_entries=LIST_FILES_SCAN_LIMIT,
        )
    truncated = len(listed) > LIST_FILES_RESULT_LIMIT
    visible = listed[:LIST_FILES_RESULT_LIMIT]
    return ActionResult(
        request.id,
        request.name,
        {"files": list(visible), "truncated": truncated},
        metadata={"count": len(visible), "truncated": truncated},
    )
