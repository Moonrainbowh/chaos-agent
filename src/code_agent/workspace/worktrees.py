from __future__ import annotations

import hashlib
import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .errors import WorkspaceError
from ._git_worktrees import FixedGitWorktreeCommands
from .git import DEFAULT_MAX_OUTPUT_BYTES, GitWorkspace
from .paths import PathInput


_LINEAGE_PATTERN = re.compile(r"[a-z0-9-]+\Z")
_BRANCH_PATTERN = re.compile(r"codex/task-[a-z0-9-]+\Z")
_DEFAULT_MAX_PATH_CHARS = 240 if os.name == "nt" else 4096


@dataclass(frozen=True)
class RepositoryIdentity:
    repository_id: str
    common_dir: Path


@dataclass(frozen=True)
class ManagedWorktree:
    repository_id: str
    lineage_id: str
    source_root: Path
    root: Path
    branch_name: str
    head_commit: str


class WorktreeManager:
    """Create and retire task worktrees through fixed Git operations."""

    def __init__(
        self,
        storage_root: PathInput,
        *,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        timeout_s: float = 30.0,
        max_path_chars: int = _DEFAULT_MAX_PATH_CHARS,
    ) -> None:
        root = Path(storage_root).expanduser()
        if _is_link_like(root):
            raise WorkspaceError("worktree storage root cannot be a link or reparse point")
        if not root.exists() or not root.is_dir():
            raise ValueError("worktree storage root must be an existing directory")
        self.storage_root = root.resolve(strict=True)
        _validate_git_limits(max_output_bytes, timeout_s)
        self.max_output_bytes = max_output_bytes
        self.timeout_s = float(timeout_s)
        if not isinstance(max_path_chars, int) or isinstance(max_path_chars, bool):
            raise TypeError("max_path_chars must be an integer")
        if max_path_chars <= 0:
            raise ValueError("max_path_chars must be positive")
        self.max_path_chars = max_path_chars

    def identify(self, source_root: PathInput) -> RepositoryIdentity:
        git = self._git(source_root)
        if not git.is_repository():
            raise WorkspaceError("source root is not a Git worktree")
        common_dir = FixedGitWorktreeCommands(git).common_dir()
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
        head_commit = commands.head_commit()
        status = git.status_porcelain()
        if commands.branch_exists(branch_name):
            raise WorkspaceError(f"managed worktree branch already exists: {branch_name}")
        target = self._prepare_target(identity.repository_id, lineage_id)
        try:
            commands.add(branch_name, target, head_commit)
            self._verify_source_unchanged(git, commands, head_commit, status)
        except Exception as error:
            self._compensate_create(git, identity, target, branch_name, head_commit, error)
            raise
        return ManagedWorktree(
            identity.repository_id,
            lineage_id,
            source,
            target.resolve(strict=True),
            branch_name,
            head_commit,
        )

    def remove(
        self, worktree: ManagedWorktree, *, confirmed: bool, active: bool
    ) -> None:
        if not confirmed:
            raise WorkspaceError("managed worktree removal requires confirmation")
        if active:
            raise WorkspaceError("active managed worktree cannot be removed")
        source_git, identity = self._require_trusted_record(worktree)
        if not self._is_owned_worktree(worktree.root, identity, worktree.branch_name):
            raise WorkspaceError("managed path does not belong to the recorded repository")
        if self._git(worktree.root).status_porcelain():
            raise WorkspaceError("dirty managed worktree cannot be removed")
        FixedGitWorktreeCommands(source_git).remove(worktree.root)
        if worktree.root.exists() or worktree.root.is_symlink():
            raise WorkspaceError("managed worktree path still exists after Git removal")

    def prune(self, records: Iterable[ManagedWorktree]) -> tuple[Path, ...]:
        groups: dict[Path, tuple[FixedGitWorktreeCommands, set[Path]]] = {}
        for record in records:
            trusted = self._trusted_missing_record(record)
            if trusted is None:
                continue
            commands, common_dir = trusted
            group = groups.setdefault(common_dir, (commands, set()))
            group[1].add(record.root)
        pruned: list[Path] = []
        for commands, trusted_roots in groups.values():
            stale = {entry.root for entry in commands.entries() if entry.prunable}
            if not stale or not stale.issubset(trusted_roots):
                continue
            commands.prune()
            pruned.extend(sorted(stale, key=str))
        return tuple(pruned)

    def _prepare_target(self, repository_id: str, lineage_id: str) -> Path:
        target = self.storage_root / repository_id / lineage_id
        if len(str(target)) > self.max_path_chars:
            raise WorkspaceError("managed worktree target exceeds path length limit")
        self._require_contained_unlinked(target)
        if target.exists() or target.is_symlink():
            raise WorkspaceError(f"managed worktree target already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        self._require_contained_unlinked(target)
        return target

    def _require_contained_unlinked(self, target: Path) -> None:
        try:
            target.resolve(strict=False).relative_to(self.storage_root)
        except (OSError, RuntimeError, ValueError) as error:
            raise WorkspaceError("managed worktree target escapes storage root") from error
        current = self.storage_root
        for part in target.relative_to(self.storage_root).parts:
            current /= part
            if _is_link_like(current):
                raise WorkspaceError(f"linked managed worktree path is not allowed: {current}")

    @staticmethod
    def _verify_source_unchanged(
        git: GitWorkspace,
        commands: FixedGitWorktreeCommands,
        head: str,
        status: str,
    ) -> None:
        if commands.head_commit() != head or git.status_porcelain() != status:
            raise WorkspaceError("source worktree changed during managed worktree creation")

    def _compensate_create(
        self,
        git: GitWorkspace,
        identity: RepositoryIdentity,
        target: Path,
        branch: str,
        head: str,
        primary: Exception,
    ) -> None:
        cleanup_errors: list[str] = []
        if self._is_owned_worktree(target, identity, branch):
            try:
                FixedGitWorktreeCommands(git).force_remove(target)
            except WorkspaceError as error:
                cleanup_errors.append(str(error))
        try:
            commands = FixedGitWorktreeCommands(git)
            if commands.branch_tip(branch) == head and not target.exists():
                commands.delete_branch(branch)
        except WorkspaceError as error:
            cleanup_errors.append(str(error))
        if cleanup_errors and hasattr(primary, "add_note"):
            primary.add_note("creation cleanup failed: " + "; ".join(cleanup_errors))

    def _is_owned_worktree(
        self, target: Path, identity: RepositoryIdentity, branch: str
    ) -> bool:
        try:
            marker = target / ".git"
            target_git = self._git(target)
            commands = FixedGitWorktreeCommands(target_git)
            return (
                marker.is_file()
                and commands.common_dir() == identity.common_dir
                and commands.current_branch() == branch
            )
        except (OSError, ValueError, WorkspaceError):
            return False

    def _require_trusted_record(
        self, record: ManagedWorktree
    ) -> tuple[GitWorkspace, RepositoryIdentity]:
        trusted = self._trusted_record(record)
        if trusted is None:
            raise WorkspaceError("managed worktree record is outside storage or has unknown repository")
        return trusted

    def _trusted_record(
        self, record: ManagedWorktree
    ) -> tuple[GitWorkspace, RepositoryIdentity] | None:
        try:
            self._validate_names(record.lineage_id, record.branch_name)
            expected = self.storage_root / record.repository_id / record.lineage_id
            if Path(os.path.abspath(record.root)) != expected or record.root == record.source_root:
                return None
            self._require_contained_unlinked(expected)
            source_git = self._git(record.source_root)
            identity = self.identify(record.source_root)
            if identity.repository_id != record.repository_id:
                return None
            return source_git, identity
        except (OSError, TypeError, ValueError, WorkspaceError):
            return None

    def _trusted_missing_record(
        self, record: ManagedWorktree
    ) -> tuple[FixedGitWorktreeCommands, Path] | None:
        trusted = self._trusted_record(record)
        if trusted is None or record.root.exists() or record.root.is_symlink():
            return None
        source_git, identity = trusted
        return FixedGitWorktreeCommands(source_git), identity.common_dir

    def _git(self, root: PathInput) -> GitWorkspace:
        return GitWorkspace(
            root,
            max_output_bytes=self.max_output_bytes,
            timeout_s=self.timeout_s,
        )

    @staticmethod
    def _validate_names(lineage_id: str, branch_name: str) -> None:
        if not isinstance(lineage_id, str) or not _LINEAGE_PATTERN.fullmatch(lineage_id):
            raise WorkspaceError("lineage id must contain lowercase letters, digits, and hyphens")
        if not isinstance(branch_name, str) or not _BRANCH_PATTERN.fullmatch(branch_name):
            raise WorkspaceError("branch must match codex/task-[a-z0-9-]+")
        if branch_name != f"codex/task-{lineage_id}":
            raise WorkspaceError("branch must be bound to the lineage id")


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as error:
        raise WorkspaceError(f"cannot inspect managed path metadata: {path}") from error
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _validate_git_limits(max_output_bytes: int, timeout_s: float) -> None:
    if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool):
        raise TypeError("max_output_bytes must be an integer")
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise TypeError("timeout_s must be a number")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be positive and finite")
