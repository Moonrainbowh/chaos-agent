"""Decide whether a task runs in the local workspace or an isolated worktree.

The local workspace is the default execution environment. A managed Git
worktree is an *isolation* mechanism for parallel or explicitly isolated
tasks, not a prerequisite for starting a task. Only a resolved plan that asks
for isolation may touch Git at all, so ordinary tasks never enumerate or copy
a dirty workspace.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import Enum


ENV_VARIABLE = "CHAOS_WORKSPACE_MODE"
_SUPPORTED = ("auto", "managed", "direct")


class WorkspaceMode(str, Enum):
    """Requested workspace strategy, before repository and task facts."""

    AUTO = "auto"
    MANAGED = "managed"
    DIRECT = "direct"


DEFAULT_MODE = WorkspaceMode.AUTO


@dataclass(frozen=True)
class WorkspaceIsolationRequest:
    """Facts about one task that may require an isolated workspace.

    ``auto`` isolates only when at least one flag is set, so an ordinary task
    pays nothing. A caller that can prove a reason sets the matching flag —
    through :func:`adaptive_isolation`, through :func:`isolated_tasks`, or
    through ``--isolated`` — before a workspace is chosen.
    """

    parallel: bool = False
    background: bool = False
    explicit: bool = False

    @property
    def requested(self) -> bool:
        """Return whether any reason to isolate this task was declared."""
        return self.parallel or self.background or self.explicit

    def reason(self) -> str:
        """Return the first declared reason, or an empty string."""
        if self.parallel:
            return "parallel"
        if self.background:
            return "background"
        if self.explicit:
            return "explicit"
        return ""


NO_ISOLATION = WorkspaceIsolationRequest()


def adaptive_isolation(
    *,
    concurrent_writer: bool = False,
    delegated: bool = False,
    explicit: bool = False,
) -> WorkspaceIsolationRequest:
    """Map observed task facts to the isolation reason `auto` should honor.

    ``concurrent_writer`` means another task is already writing this root, so
    the newcomer is parallel by observation rather than by declaration.
    ``delegated`` means the caller runs the task out of band while the user may
    keep editing the source workspace. ``explicit`` means the caller asked for
    isolation by name. All three are facts known before a workspace is chosen,
    which is what makes `auto` adaptive rather than a guess.
    """
    return WorkspaceIsolationRequest(
        parallel=concurrent_writer, background=delegated, explicit=explicit
    )


EXPLICIT_ISOLATION = adaptive_isolation(explicit=True)


# --- ambient isolation scopes ------------------------------------------------
#
# The runtime cannot always prove, at the moment it picks a workspace, that a
# task is isolated. The caller that *does* know says so for the duration of the
# work it starts, and the scope reaches workspace selection through the context
# instead of through every constructor in between. ``--isolated`` uses the
# ``explicit`` scope; a background or delegated runner uses ``background``.

_SCOPE: ContextVar[str | None] = ContextVar("chaos_workspace_isolation", default=None)
_SCOPES = ("explicit", "background")


def request_task_isolation(reason: str) -> Token[str | None]:
    """Declare why tasks started in this context must run isolated.

    Returns a token for :func:`reset_task_isolation`. Prefer the
    :func:`isolated_tasks` context manager, which pairs the two.
    """
    if reason not in _SCOPES:
        raise ValueError(f"isolation scope must be one of: {', '.join(_SCOPES)}")
    return _SCOPE.set(reason)


def reset_task_isolation(token: Token[str | None]) -> None:
    """Restore the isolation scope that was active before ``token``."""
    _SCOPE.reset(token)


def task_isolation_request() -> WorkspaceIsolationRequest:
    """Return the isolation reason the active scope declared, if any."""
    reason = _SCOPE.get()
    if reason is None:
        return NO_ISOLATION
    return adaptive_isolation(
        explicit=reason == "explicit", delegated=reason == "background"
    )


@contextmanager
def isolated_tasks(reason: str) -> Iterator[None]:
    """Run a block whose tasks must use an isolated managed worktree."""
    token = request_task_isolation(reason)
    try:
        yield
    finally:
        reset_task_isolation(token)


@dataclass(frozen=True)
class WorkspacePlan:
    """A resolved decision: where one task is allowed to read and write."""

    mode: WorkspaceMode
    isolated: bool
    reason: str


def workspace_mode(env: Mapping[str, str] | None = None) -> WorkspaceMode:
    """Return the requested mode, rejecting values outside the contract."""
    source = os.environ if env is None else env
    raw = source.get(ENV_VARIABLE)
    value = DEFAULT_MODE.value if raw is None else str(raw).strip().lower()
    if value not in _SUPPORTED:
        raise ValueError(
            f"{ENV_VARIABLE} must be one of: {', '.join(_SUPPORTED)}"
        )
    return WorkspaceMode(value)


def repository_probe_required(
    mode: WorkspaceMode,
    isolation: WorkspaceIsolationRequest = NO_ISOLATION,
) -> bool:
    """Return whether resolving this plan needs a Git repository probe.

    ``direct`` never needs one, and ``auto`` only needs one once the task
    actually asked for isolation. This keeps the default path free of any Git
    subprocess, not just free of snapshot work.
    """
    if mode is WorkspaceMode.DIRECT:
        return False
    if mode is WorkspaceMode.MANAGED:
        return True
    return isolation.requested


def resolve_workspace_plan(
    mode: WorkspaceMode,
    *,
    is_git_repository: bool,
    isolation: WorkspaceIsolationRequest = NO_ISOLATION,
) -> WorkspacePlan:
    """Resolve a mode plus task facts into a concrete workspace plan.

    Only ``managed``, and ``auto`` with a declared isolation reason, may
    select a worktree. A non-repository workspace cannot host a worktree, so
    it stays local and says why.
    """
    if mode is WorkspaceMode.DIRECT:
        return WorkspacePlan(mode, False, "direct-mode")
    if mode is WorkspaceMode.MANAGED:
        if not is_git_repository:
            return WorkspacePlan(
                mode, False, "managed-requires-git-repository"
            )
        return WorkspacePlan(mode, True, "managed-mode")
    if not isolation.requested:
        return WorkspacePlan(mode, False, "auto-local-workspace")
    if not is_git_repository:
        return WorkspacePlan(
            mode, False, "auto-isolation-requires-git-repository"
        )
    return WorkspacePlan(mode, True, f"auto-isolation:{isolation.reason()}")


def plan_workspace(
    is_git_repository: Callable[[], bool],
    *,
    mode: WorkspaceMode,
    isolation: WorkspaceIsolationRequest = NO_ISOLATION,
) -> WorkspacePlan:
    """Resolve a plan for one root, probing Git only when required.

    ``is_git_repository`` is evaluated lazily, so the default local-workspace
    path performs no Git work at all.
    """
    if not repository_probe_required(mode, isolation):
        return resolve_workspace_plan(
            mode, is_git_repository=False, isolation=isolation
        )
    return resolve_workspace_plan(
        mode,
        is_git_repository=bool(is_git_repository()),
        isolation=isolation,
    )


__all__ = [
    "DEFAULT_MODE",
    "ENV_VARIABLE",
    "EXPLICIT_ISOLATION",
    "NO_ISOLATION",
    "WorkspaceIsolationRequest",
    "WorkspaceMode",
    "WorkspacePlan",
    "adaptive_isolation",
    "isolated_tasks",
    "plan_workspace",
    "repository_probe_required",
    "request_task_isolation",
    "reset_task_isolation",
    "resolve_workspace_plan",
    "task_isolation_request",
    "workspace_mode",
]
