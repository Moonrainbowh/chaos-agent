"""Give a local task the workspace identity that durable checkpoints require.

Executable checkpoints, rewind, and session rewind are keyed by a workspace
lineage. A task that runs directly in the source workspace owns a lineage
whose worktree root *is* that source root, so it can checkpoint and rewind
without a managed worktree. Nothing else is created: no branch, no directory,
and no dirty-state copy.

The only Git work here is constant-size (``rev-parse`` for the common dir, HEAD,
and the current branch). It never depends on how many files are dirty.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path

from code_agent.sessions.workspace_models import WorkspaceLineageRecord
from code_agent.workspace._git_worktrees import FixedGitWorktreeCommands
from code_agent.workspace.errors import WorkspaceError
from code_agent.workspace.git import GitWorkspace


DETACHED_BRANCH = "detached-head"
"""Branch label used when the source root has no checked-out branch."""


async def attach_local_lineage(
    sessions: object, root: Path, task_id: str
) -> bool:
    """Persist a worktree-free lineage for ``task_id`` when ``root`` allows it.

    Returns whether the task can write durable checkpoints. A root that cannot
    host a lineage keeps metadata-only checkpoints instead of losing the task.
    """
    record = await asyncio.to_thread(build_local_lineage, root.resolve(), task_id)
    if record is None:
        return False
    await sessions.create_lineage(record)
    return True


def build_local_lineage(
    root: Path, task_id: str
) -> WorkspaceLineageRecord | None:
    """Return a worktree-free lineage for ``task_id``, or ``None``.

    ``None`` means the root has no repository, no commit yet, or Git refused
    the facts the record requires.
    """
    facts = _repository_facts(root)
    if facts is None:
        return None
    repository_id, head_commit, branch_name = facts
    return WorkspaceLineageRecord(
        uuid.uuid4().hex,
        repository_id=repository_id,
        source_root=str(root),
        worktree_root=str(root),
        branch_name=branch_name,
        head_commit=head_commit,
        owner_task_id=task_id,
    )


def _repository_facts(root: Path) -> tuple[str, str, str] | None:
    try:
        git = GitWorkspace(root)
        if not git.is_repository():
            return None
        commands = FixedGitWorktreeCommands(git)
        common_dir = commands.common_dir()
        head_commit = commands.head_commit()
        branch_name = commands.current_branch() or DETACHED_BRANCH
    except (WorkspaceError, OSError, ValueError):
        return None
    normalized = os.path.normcase(str(common_dir))
    repository_id = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return repository_id, head_commit, branch_name


__all__ = ["DETACHED_BRANCH", "attach_local_lineage", "build_local_lineage"]
