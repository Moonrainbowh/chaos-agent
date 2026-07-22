from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
