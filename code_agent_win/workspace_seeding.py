"""Copy a source workspace's uncommitted state into a managed worktree.

This is the one operation that enumerates tracked changes and untracked files
and then copies them. Only an isolated task pays it, and only because a
worktree starts from the committed tree: without a seed, the task would not see
the user's in-progress edits.

Kept apart from the workspace runtime so that "does an ordinary task copy a
dirty workspace?" stays answerable by reading one small module.
"""

from __future__ import annotations

from pathlib import Path

from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.git import GitWorkspace
from code_agent.workspace.paths import WorkspacePathGuard


def seed_source_changes(
    source_root: Path,
    target_root: Path,
    *,
    allow_sensitive_paths: bool = False,
) -> None:
    """Restore the source's changed and eligible untracked code into a worktree.

    ``changed_snapshot_paths`` is bounded by the workspace's Git output limit,
    so a repository with very many dirty files fails loudly here instead of
    silently copying a partial snapshot.
    """
    paths = GitWorkspace(source_root).changed_snapshot_paths()
    snapshot = _editor(source_root, allow_sensitive_paths).snapshot(paths)
    _editor(target_root, allow_sensitive_paths).restore(snapshot)


def _editor(root: Path, allow_sensitive_paths: bool) -> WorkspaceEditor:
    return WorkspaceEditor(
        WorkspacePathGuard(root, allow_sensitive=allow_sensitive_paths)
    )


__all__ = ["seed_source_changes"]
