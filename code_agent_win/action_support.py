from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable

from code_agent.core.models import ActionRequest, ActionResult
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.git import GitWorkspace


LIST_FILES_RESULT_LIMIT = 200
LIST_FILES_SCAN_LIMIT = 200_000


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
