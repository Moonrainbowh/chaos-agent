from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

from ._git_worktrees import FixedGitWorktreeCommands, GitWorktreeEntry
from .errors import WorkspaceError
from ._worktree_lock import valid_repository_id


def remove_managed(
    manager: Any, worktree: Any, *, confirmed: bool, active: bool
) -> None:
    del confirmed, active
    source_git, identity = _require_trusted_record(manager, worktree)
    if not manager._is_owned_worktree(worktree.root, identity, worktree.branch_name):
        raise WorkspaceError("managed path does not belong to the recorded repository")
    if manager._git(worktree.root).status_porcelain():
        raise WorkspaceError("dirty managed worktree cannot be removed")
    FixedGitWorktreeCommands(source_git).remove(worktree.root)
    if worktree.root.exists() or worktree.root.is_symlink():
        raise WorkspaceError("managed worktree path still exists after Git removal")


def validate_remove_request(*, confirmed: bool, active: bool) -> None:
    if confirmed is not True:
        raise WorkspaceError("managed worktree removal requires literal confirmation")
    if type(active) is not bool:
        raise WorkspaceError("managed worktree active state must be boolean")
    if active:
        raise WorkspaceError("active managed worktree cannot be removed")


def prune_managed(manager: Any, records: Iterable[Any]) -> tuple[Path, ...]:
    pruned: list[Path] = []
    for record in records:
        repository_id = getattr(record, "repository_id", None)
        if not valid_repository_id(repository_id):
            continue
        with manager._repository_lock(repository_id):
            if _prune_record(manager, record):
                pruned.append(record.root)
    return tuple(pruned)


def _prune_record(manager: Any, record: Any) -> bool:
    commands = _trusted_missing_record(manager, record)
    if commands is None:
        return False
    entry = commands.entry(record.root)
    if not _matches_record(entry, record):
        return False
    if record.root.exists() or record.root.is_symlink():
        return False
    try:
        commands.remove_missing(record.root)
    except WorkspaceError:
        return False
    return commands.entry(record.root) is None


def _matches_record(entry: GitWorktreeEntry | None, record: Any) -> bool:
    return bool(
        entry is not None
        and entry.prunable
        and entry.branch == f"refs/heads/{record.branch_name}"
    )


def attach_cleanup_errors(primary: Exception, errors: list[str]) -> None:
    if not errors:
        return
    setattr(primary, "cleanup_errors", tuple(errors))
    if hasattr(primary, "add_note"):
        primary.add_note("creation cleanup failed: " + "; ".join(errors))


def _require_trusted_record(manager: Any, record: Any):
    trusted = _trusted_record(manager, record)
    if trusted is None:
        raise WorkspaceError(
            "managed worktree record is outside storage or has unknown repository"
        )
    return trusted


def _trusted_record(manager: Any, record: Any):
    try:
        manager._validate_names(record.lineage_id, record.branch_name)
        expected = manager.storage_root / record.repository_id / record.lineage_id
        if _literal_key(record.root) != _literal_key(expected):
            return None
        if _literal_key(record.root) == _literal_key(record.source_root):
            return None
        manager._require_contained_unlinked(expected)
        source_git = manager._git(record.source_root)
        identity = manager.identify(record.source_root)
        if identity.repository_id != record.repository_id:
            return None
        return source_git, identity
    except (OSError, TypeError, ValueError, WorkspaceError):
        return None


def _trusted_missing_record(manager: Any, record: Any):
    trusted = _trusted_record(manager, record)
    if trusted is None or record.root.exists() or record.root.is_symlink():
        return None
    source_git, _identity = trusted
    return FixedGitWorktreeCommands(source_git)


def _literal_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))
