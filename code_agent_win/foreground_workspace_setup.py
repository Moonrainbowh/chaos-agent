from __future__ import annotations

from pathlib import Path

from code_agent.workspace.errors import WorkspaceError


async def prepare_workspace(runtime: object | None, root: Path):
    if runtime is None:
        return None
    from code_agent_win.tool_support import discover_git_workspace

    if discover_git_workspace(root) is None:
        return None
    try:
        return await runtime.prepare_task(root, "pending")
    except WorkspaceError as error:
        raise RuntimeError("managed task worktree could not be prepared") from error


async def bind_workspace(
    runtime: object | None,
    thread_id: str,
    task_id: str,
    root: Path,
    workspace: object | None,
) -> None:
    if runtime is None:
        return
    if workspace is not None:
        await runtime.create_lineage(workspace, task_id)
        root = workspace.worktree_root
    runtime.bind_task(task_id, root)
    runtime.bind_thread(thread_id, root)


async def abort_prepared_workspace(
    runtime: object | None,
    workspace: object | None,
    primary: BaseException,
) -> None:
    if runtime is None or workspace is None:
        return
    try:
        await runtime.abort_prepared_task(workspace)
    except BaseException as cleanup:
        add_note = getattr(primary, "add_note", None)
        if callable(add_note):
            add_note(f"prepared worktree cleanup failed: {cleanup}")
