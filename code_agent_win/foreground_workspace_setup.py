from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from code_agent.workspace.errors import WorkspaceError

from code_agent_win.local_workspace_lineage import attach_local_lineage
from code_agent_win.workspace_policy import (
    NO_ISOLATION,
    WorkspaceIsolationRequest,
    adaptive_isolation,
    plan_workspace,
    task_isolation_request,
    workspace_mode,
)


CONCURRENT_TASK_ISOLATION = adaptive_isolation(concurrent_writer=True)


def resolve_task_workspace_plan(
    root: Path,
    *,
    isolation: WorkspaceIsolationRequest = NO_ISOLATION,
):
    """Decide where this task runs without paying for isolation up front.

    ``direct`` and the ordinary ``auto`` path return a local-workspace plan
    without probing Git, so no task startup enumerates or copies a dirty
    workspace. Only a plan that actually asks for isolation may read Git.
    """
    from code_agent_win.tool_support import discover_git_workspace

    return plan_workspace(
        lambda: discover_git_workspace(root) is not None,
        mode=workspace_mode(),
        isolation=isolation,
    )


def isolation_available(root: Path, isolation: WorkspaceIsolationRequest) -> bool:
    """Return whether ``root`` can honor ``isolation``.

    Only reached once something already asked for isolation, so the Git probe
    it may perform is never part of an ordinary task's startup path.
    """
    return resolve_task_workspace_plan(root, isolation=isolation).isolated


def require_isolation(root: Path, isolation: WorkspaceIsolationRequest) -> None:
    """Refuse a task whose declared isolation this root cannot provide.

    The reason names the cause — ``direct`` mode, or a root that cannot host a
    worktree — so the caller learns why isolation was withheld instead of
    silently running in the source workspace.
    """
    plan = resolve_task_workspace_plan(root, isolation=isolation)
    if not plan.isolated:
        raise RuntimeError(
            f"isolated workspace was requested but is unavailable: {plan.reason}"
        )


async def task_workspace_isolation(
    root: Path, is_root_busy: Callable[[], Awaitable[bool]]
) -> WorkspaceIsolationRequest:
    """Return why a task starting on ``root`` needs an isolated workspace.

    A declared reason (``--isolated``, or a delegated/background scope) always
    wins and is enforced. Otherwise a task that finds another writer on its
    root is parallel by observation: `auto` isolates it in a managed worktree
    so the root keeps a single local writer, and a mode that cannot isolate
    refuses the task instead of sharing the root.
    """
    declared = task_isolation_request()
    if declared.requested:
        require_isolation(root, declared)
        return declared
    if not await is_root_busy():
        return NO_ISOLATION
    if not isolation_available(root, CONCURRENT_TASK_ISOLATION):
        raise RuntimeError("a foreground task is already active")
    return CONCURRENT_TASK_ISOLATION



async def prepare_workspace(
    runtime: object | None,
    root: Path,
    *,
    isolation: WorkspaceIsolationRequest = NO_ISOLATION,
):
    """Return a prepared worktree, or ``None`` for the local workspace.

    Every foreground task is single-writer and interactive, so it calls this
    with the default (no isolation reason) and `auto` resolves to the source
    workspace. Parallel or background execution must pass an explicit
    ``WorkspaceIsolationRequest`` here, which `auto` then resolves to a managed
    worktree on its own — no other call site changes.
    """
    if runtime is None:
        return None
    plan = resolve_task_workspace_plan(root, isolation=isolation)
    if not plan.isolated:
        return None
    try:
        return await runtime.prepare_task(root, "pending")
    except WorkspaceError as error:
        raise RuntimeError(
            f"managed task worktree could not be prepared: {error}"
        ) from error


async def bind_workspace(
    runtime: object | None,
    thread_id: str,
    task_id: str,
    root: Path,
    workspace: object | None,
) -> None:
    """Bind one started task to its workspace identity.

    A managed task owns a worktree lineage. A local task owns a worktree-free
    lineage instead, so it can still write executable checkpoints; the runtime
    keeps its own session store, which is where that record lives.
    """
    if runtime is None:
        return
    if workspace is not None:
        await runtime.create_lineage(workspace, task_id)
        root = workspace.worktree_root
    else:
        await attach_local_lineage(runtime._sessions, root, task_id)
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
