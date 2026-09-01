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


def discard_unclaimed(manager: Any, worktree: Any) -> None:
    """Remove an exact, not-yet-persisted worktree even when seed files are dirty."""
    source_git, identity = _require_trusted_record(manager, worktree)
    commands = FixedGitWorktreeCommands(source_git)
    entry = commands.entry(worktree.root)
    expected_ref = f"refs/heads/{worktree.branch_name}"
    if (
        entry is None
        or entry.prunable
        or entry.branch != expected_ref
        or entry.head != worktree.head_commit
    ):
        raise WorkspaceError("unclaimed worktree registration no longer matches its record")
    if commands.branch_tip(worktree.branch_name) != worktree.head_commit:
        raise WorkspaceError("unclaimed worktree branch no longer matches its recorded HEAD")
    if not manager._is_owned_worktree(
        worktree.root, identity, worktree.branch_name
    ):
        raise WorkspaceError("unclaimed worktree identity cannot be proven")
    commands.force_remove(worktree.root)
    if commands.entry(worktree.root) is not None:
        raise WorkspaceError("unclaimed worktree registration still exists")
    if worktree.root.exists() or worktree.root.is_symlink():
        raise WorkspaceError("unclaimed worktree path still exists")
    if commands.branch_tip(worktree.branch_name) != worktree.head_commit:
        raise WorkspaceError("unclaimed worktree branch changed during removal")
    commands.delete_branch_if_matches(worktree.branch_name, worktree.head_commit)
    if commands.branch_exists(worktree.branch_name):
        raise WorkspaceError("unclaimed worktree branch still exists")


def compensate_create(
    manager: Any,
    git: Any,
    identity: Any,
    target: Path,
    branch: str,
    head: str,
    primary: Exception,
    *,
    added: bool,
) -> None:
    if not added:
        attach_cleanup_errors(
            primary, ["worktree add ownership was not proven; cleanup skipped"]
        )
        return
    cleanup_errors: list[str] = []
    commands = FixedGitWorktreeCommands(git)
    removed = _remove_created_registration(
        manager, commands, identity, target, branch, cleanup_errors
    )
    if removed:
        _delete_created_branch(commands, branch, head, cleanup_errors)
    attach_cleanup_errors(primary, cleanup_errors)


def _remove_created_registration(
    manager: Any,
    commands: FixedGitWorktreeCommands,
    identity: Any,
    target: Path,
    branch: str,
    errors: list[str],
) -> bool:
    try:
        manager._require_contained_unlinked(target)
        entry = commands.entry(target)
        expected_ref = f"refs/heads/{branch}"
        if commands.common_dir() != identity.common_dir:
            errors.append("cleanup skipped: repository identity changed")
            return False
        if entry is None or entry.branch != expected_ref:
            errors.append("cleanup skipped: exact created registration is unavailable")
            return False
        if not target.exists() or not target.is_dir() or target.is_symlink():
            errors.append("cleanup skipped: literal target is missing or not a directory")
            return False
        if not manager._is_owned_worktree(target, identity, branch):
            errors.append("cleanup skipped: target worktree identity cannot be proven")
            return False
        commands.force_remove(target)
        if commands.entry(target) is not None:
            errors.append("created worktree registration still exists")
            return False
        return True
    except (OSError, ValueError, WorkspaceError) as error:
        errors.append(f"cleanup skipped: {error}")
        return False


def _delete_created_branch(
    commands: FixedGitWorktreeCommands,
    branch: str,
    head: str,
    errors: list[str],
) -> None:
    try:
        if commands.branch_tip(branch) == head:
            commands.delete_branch_if_matches(branch, head)
    except WorkspaceError as error:
        errors.append(str(error))


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
