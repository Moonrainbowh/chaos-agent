from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from code_agent.context.repo_index import RepoIndexService
from code_agent.interfaces.checkpoint_control import CheckpointControl
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.verification.local_adapter import LocalVerificationAdapter
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.git import GitWorkspace
from code_agent.workspace.paths import WorkspacePathGuard


@dataclass(frozen=True)
class TaskWorkspace:
    lineage_id: str
    source_root: Path
    worktree_root: Path
    branch_name: str


@dataclass
class WorkspaceServices:
    root: Path
    guard: WorkspacePathGuard
    files: WorkspaceFiles
    git: GitWorkspace | None
    repo_index: RepoIndexService
    runtime: WindowsLocalRuntime
    verification: LocalVerificationAdapter
    checkpoints: CheckpointControl | None = None
