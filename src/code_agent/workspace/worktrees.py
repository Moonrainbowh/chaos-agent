from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterable

from .errors import WorkspaceError
from ._git_worktrees import FixedGitWorktreeCommands
from ._worktree_lifecycle_ops import (
    compensate_create,
    discard_unclaimed,
    prune_managed,
    remove_managed,
    validate_remove_request,
)
from ._worktree_lock import RepositoryLifecycleLock, validate_lock_timeout
from ._worktree_models import ManagedWorktree, RepositoryIdentity
from ._worktree_limits import (
    validate_git_limits,
    validate_worktree_names,
    worktree_path_limit,
    worktree_path_units,
)
from ._worktree_leases import UnclaimedWorktreeLease, WorktreeLeaseRegistry
from ._worktree_paths import is_link_like, require_contained_unlinked
from .git import DEFAULT_MAX_OUTPUT_BYTES, GitWorkspace
from .paths import PathInput
from .windows_paths import require_supported_windows_path


class WorktreeManager:
    """Create and retire task worktrees through fixed Git operations."""

    def __init__(
        self,
        storage_root: PathInput,
        *,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        timeout_s: float = 30.0,
        max_path_chars: int | None = None,
        lock_timeout_s: float = 5.0,
    ) -> None:
        root = Path(storage_root).expanduser()
        require_supported_windows_path(
            root,
            operation="worktree storage root",
        )
        if is_link_like(root):
            raise WorkspaceError("worktree storage root cannot be a link or reparse point")
        if not root.exists() or not root.is_dir():
            raise ValueError("worktree storage root must be an existing directory")
        self.storage_root = root.resolve(strict=True)
        require_supported_windows_path(self.storage_root, operation="worktree storage root")
        validate_git_limits(max_output_bytes, timeout_s)
        self.max_output_bytes = max_output_bytes
        self.timeout_s = float(timeout_s)
        validate_lock_timeout(lock_timeout_s)
        self.lock_timeout_s = float(lock_timeout_s)
        self.max_path_chars = worktree_path_limit(max_path_chars)
        self._leases = WorktreeLeaseRegistry()

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
        source_literal = Path(source_root).expanduser()
        require_supported_windows_path(
            source_literal,
            operation="worktree source root",
        )
        source = source_literal.resolve(strict=True)
        require_supported_windows_path(source, operation="worktree source root")
        git = self._git(source)
        commands = FixedGitWorktreeCommands(git)
        identity = self.identify(source)
        proposed_target = self.storage_root / identity.repository_id / lineage_id
        require_supported_windows_path(
            proposed_target,
            operation="managed worktree target",
        )
        if worktree_path_units(proposed_target) > self.max_path_chars:
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
            compensate_create(
                self,
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

    def create_unclaimed(
        self, source_root: PathInput, lineage_id: str, branch_name: str
    ) -> UnclaimedWorktreeLease:
        return self._leases.issue(
            self.create(source_root, lineage_id, branch_name)
        )

    def claim_unclaimed(
        self, lease: UnclaimedWorktreeLease
    ) -> ManagedWorktree:
        return self._leases.claim(lease)

    def discard_unclaimed(self, lease: UnclaimedWorktreeLease) -> None:
        self._leases.discard(lease, self._discard_unclaimed)

    def _discard_unclaimed(self, worktree: ManagedWorktree) -> None:
        with self._repository_lock(worktree.repository_id):
            discard_unclaimed(self, worktree)

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
        validate_worktree_names(lineage_id, branch_name)
