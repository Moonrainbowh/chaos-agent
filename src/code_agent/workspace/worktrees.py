from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path
from typing import Iterable

from .errors import WorkspaceError
from ._git_worktrees import FixedGitWorktreeCommands
from ._worktree_lifecycle_ops import (
    attach_cleanup_errors,
    prune_managed,
    remove_managed,
    validate_remove_request,
)
from ._worktree_lock import RepositoryLifecycleLock, validate_lock_timeout
from ._worktree_models import ManagedWorktree, RepositoryIdentity
from ._worktree_paths import is_link_like, require_contained_unlinked
from .git import DEFAULT_MAX_OUTPUT_BYTES, GitWorkspace
from .paths import PathInput


_LINEAGE_PATTERN = re.compile(r"[a-z0-9-]+\Z")
_BRANCH_PATTERN = re.compile(r"codex/task-[a-z0-9-]+\Z")
_DEFAULT_MAX_PATH_CHARS = 240 if os.name == "nt" else 4096


class WorktreeManager:
    """Create and retire task worktrees through fixed Git operations."""

    def __init__(
        self,
        storage_root: PathInput,
        *,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        timeout_s: float = 30.0,
        max_path_chars: int = _DEFAULT_MAX_PATH_CHARS,
        lock_timeout_s: float = 5.0,
    ) -> None:
        root = Path(storage_root).expanduser()
        if is_link_like(root):
            raise WorkspaceError("worktree storage root cannot be a link or reparse point")
        if not root.exists() or not root.is_dir():
            raise ValueError("worktree storage root must be an existing directory")
        self.storage_root = root.resolve(strict=True)
        _validate_git_limits(max_output_bytes, timeout_s)
        self.max_output_bytes = max_output_bytes
        self.timeout_s = float(timeout_s)
        validate_lock_timeout(lock_timeout_s)
        self.lock_timeout_s = float(lock_timeout_s)
        if not isinstance(max_path_chars, int) or isinstance(max_path_chars, bool):
            raise TypeError("max_path_chars must be an integer")
        if max_path_chars <= 0:
            raise ValueError("max_path_chars must be positive")
        self.max_path_chars = max_path_chars

    def identify(self, source_root: PathInput) -> RepositoryIdentity:
        git = self._git(source_root)
        if not git.is_repository():
            raise WorkspaceError("source root is not a Git worktree")
        commands = FixedGitWorktreeCommands(git)
        if commands.top_level() != git.root:
            raise WorkspaceError("source root must be the exact Git top-level")
        common_dir = commands.common_dir()
        normalized = os.path.normcase(str(common_dir))
        repository_id = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return RepositoryIdentity(repository_id, common_dir)

    def create(
        self, source_root: PathInput, lineage_id: str, branch_name: str
    ) -> ManagedWorktree:
        self._validate_names(lineage_id, branch_name)
        source = Path(source_root).expanduser().resolve(strict=True)
        git = self._git(source)
        commands = FixedGitWorktreeCommands(git)
        identity = self.identify(source)
        proposed_target = self.storage_root / identity.repository_id / lineage_id
        if len(str(proposed_target)) > self.max_path_chars:
            raise WorkspaceError("managed worktree target exceeds path length limit")
        with self._repository_lock(identity.repository_id):
            return self._create_locked(
                source, git, commands, identity, lineage_id, branch_name
            )

    def _create_locked(
        self,
        source: Path,
        git: GitWorkspace,
        commands: FixedGitWorktreeCommands,
        identity: RepositoryIdentity,
        lineage_id: str,
        branch_name: str,
    ) -> ManagedWorktree:
        head_commit = commands.head_commit()
        status = git.status_porcelain()
        if commands.branch_exists(branch_name):
            raise WorkspaceError(f"managed worktree branch already exists: {branch_name}")
        target = self._prepare_target(identity.repository_id, lineage_id)
        added = False
        try:
            commands.add(branch_name, target, head_commit)
            added = True
            self._verify_created(
                git, commands, identity, target, branch_name, head_commit, status
            )
        except Exception as error:
            self._compensate_create(
                git, identity, target, branch_name, head_commit, error, added=added
            )
            raise
        return ManagedWorktree(
            identity.repository_id,
            lineage_id,
            source,
            target,
            branch_name,
            head_commit,
        )

    def remove(
        self, worktree: ManagedWorktree, *, confirmed: bool, active: bool
    ) -> None:
        validate_remove_request(confirmed=confirmed, active=active)
        with self._repository_lock(worktree.repository_id):
            remove_managed(self, worktree, confirmed=confirmed, active=active)

    def prune(self, records: Iterable[ManagedWorktree]) -> tuple[Path, ...]:
        return prune_managed(self, records)

    def _prepare_target(self, repository_id: str, lineage_id: str) -> Path:
        target = self.storage_root / repository_id / lineage_id
        self._require_contained_unlinked(target)
        if target.exists() or target.is_symlink():
            raise WorkspaceError(f"managed worktree target already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        self._require_contained_unlinked(target)
        return target

    def _require_contained_unlinked(self, target: Path) -> None:
        require_contained_unlinked(self.storage_root, target)

    @staticmethod
    def _verify_source_unchanged(
        git: GitWorkspace,
        commands: FixedGitWorktreeCommands,
        head: str,
        status: str,
    ) -> None:
        if commands.head_commit() != head or git.status_porcelain() != status:
            raise WorkspaceError("source worktree changed during managed worktree creation")

    def _verify_created(
        self,
        source_git: GitWorkspace,
        source_commands: FixedGitWorktreeCommands,
        identity: RepositoryIdentity,
        target: Path,
        branch: str,
        head: str,
        status: str,
    ) -> None:
        self._verify_source_unchanged(source_git, source_commands, head, status)
        self._require_contained_unlinked(target)
        if not target.exists() or not target.is_dir() or target.is_symlink():
            raise WorkspaceError("managed worktree target failed postcheck")
        target_git = self._git(target)
        target_commands = FixedGitWorktreeCommands(target_git)
        target_identity = self.identify(target)
        if target_identity != identity:
            raise WorkspaceError("managed worktree repository failed postcheck")
        if target_commands.head_commit() != head or target_commands.current_branch() != branch:
            raise WorkspaceError("managed worktree HEAD or branch failed postcheck")
        entry = source_commands.entry(target)
        expected_ref = f"refs/heads/{branch}"
        if entry is None or entry.prunable or entry.head != head or entry.branch != expected_ref:
            raise WorkspaceError("managed worktree registration failed postcheck")

    def _compensate_create(
        self,
        git: GitWorkspace,
        identity: RepositoryIdentity,
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
        removed = self._remove_created_registration(
            commands, identity, target, branch, cleanup_errors
        )
        if removed:
            self._delete_created_branch(commands, branch, head, cleanup_errors)
        attach_cleanup_errors(primary, cleanup_errors)

    def _remove_created_registration(
        self,
        commands: FixedGitWorktreeCommands,
        identity: RepositoryIdentity,
        target: Path,
        branch: str,
        errors: list[str],
    ) -> bool:
        try:
            self._require_contained_unlinked(target)
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
            if not self._is_owned_worktree(target, identity, branch):
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

    @staticmethod
    def _delete_created_branch(
        commands: FixedGitWorktreeCommands,
        branch: str,
        head: str,
        errors: list[str],
    ) -> None:
        try:
            if commands.branch_tip(branch) == head:
                commands.delete_branch(branch)
        except WorkspaceError as error:
            errors.append(str(error))

    def _is_owned_worktree(
        self, target: Path, identity: RepositoryIdentity, branch: str | None
    ) -> bool:
        try:
            marker = target / ".git"
            target_git = self._git(target)
            commands = FixedGitWorktreeCommands(target_git)
            return (
                marker.is_file()
                and commands.common_dir() == identity.common_dir
                and (branch is None or commands.current_branch() == branch)
            )
        except (OSError, ValueError, WorkspaceError):
            return False

    def _git(self, root: PathInput) -> GitWorkspace:
        return GitWorkspace(
            root,
            max_output_bytes=self.max_output_bytes,
            timeout_s=self.timeout_s,
        )

    def _repository_lock(self, repository_id: str) -> RepositoryLifecycleLock:
        return RepositoryLifecycleLock(
            self.storage_root, repository_id, self.lock_timeout_s
        )

    @staticmethod
    def _validate_names(lineage_id: str, branch_name: str) -> None:
        if not isinstance(lineage_id, str) or not _LINEAGE_PATTERN.fullmatch(lineage_id):
            raise WorkspaceError("lineage id must contain lowercase letters, digits, and hyphens")
        if not isinstance(branch_name, str) or not _BRANCH_PATTERN.fullmatch(branch_name):
            raise WorkspaceError("branch must match codex/task-[a-z0-9-]+")
        if branch_name != f"codex/task-{lineage_id}":
            raise WorkspaceError("branch must be bound to the lineage id")

def _validate_git_limits(max_output_bytes: int, timeout_s: float) -> None:
    if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool):
        raise TypeError("max_output_bytes must be an integer")
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise TypeError("timeout_s must be a number")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be positive and finite")
