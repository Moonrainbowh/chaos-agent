from __future__ import annotations

import math
import os
import re
from pathlib import Path

from .errors import WorkspaceError
from .windows_paths import windows_path_units


WINDOWS_GIT_WORKTREE_MAX_PATH_UNITS = 215
POSIX_MAX_PATH_CHARS = 4096
_LINEAGE_PATTERN = re.compile(r"[a-z0-9-]+\Z")
_BRANCH_PATTERN = re.compile(r"codex/task-[a-z0-9-]+\Z")


def worktree_path_limit(value: int | None) -> int:
    if value is None:
        value = (
            WINDOWS_GIT_WORKTREE_MAX_PATH_UNITS
            if os.name == "nt"
            else POSIX_MAX_PATH_CHARS
        )
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("max_path_chars must be an integer")
    if value <= 0:
        raise ValueError("max_path_chars must be positive")
    if os.name == "nt":
        return min(value, WINDOWS_GIT_WORKTREE_MAX_PATH_UNITS)
    return value


def worktree_path_units(path: Path) -> int:
    if os.name != "nt":
        return len(str(path))
    return max(
        windows_path_units(path),
        len(str(path).encode("utf-8")),
    )


def validate_git_limits(max_output_bytes: int, timeout_s: float) -> None:
    if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool):
        raise TypeError("max_output_bytes must be an integer")
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise TypeError("timeout_s must be a number")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be positive and finite")


def validate_worktree_names(lineage_id: str, branch_name: str) -> None:
    if not isinstance(lineage_id, str) or not _LINEAGE_PATTERN.fullmatch(lineage_id):
        raise WorkspaceError(
            "lineage id must contain lowercase letters, digits, and hyphens"
        )
    if not isinstance(branch_name, str) or not _BRANCH_PATTERN.fullmatch(branch_name):
        raise WorkspaceError("branch must match codex/task-[a-z0-9-]+")
    if branch_name != f"codex/task-{lineage_id}":
        raise WorkspaceError("branch must be bound to the lineage id")
