from __future__ import annotations

import os
from pathlib import Path


MAX_PROJECT_MARKER_SCAN = 512
_PROJECT_MARKERS = (
    "build.gradle",
    "build.gradle.kts",
    "Cargo.toml",
    "CMakeLists.txt",
    "go.mod",
    "Makefile",
    "package.json",
    "pnpm-workspace.yaml",
    "pom.xml",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
)
_PROJECT_SUFFIXES = (".csproj", ".fsproj", ".sln", ".vcxproj")


def workspace_uses_repo_map(root: Path, git_available: bool) -> bool:
    """Enable automatic repository maps only for bounded project roots."""
    resolved = Path(root).resolve(strict=False)
    if _is_broad_root(resolved):
        return False
    if git_available:
        return True
    for marker in _PROJECT_MARKERS:
        if _is_regular_file(resolved / marker):
            return True
    try:
        with os.scandir(resolved) as entries:
            for _ in range(MAX_PROJECT_MARKER_SCAN):
                try:
                    child = next(entries)
                except StopIteration:
                    return False
                if not child.name.casefold().endswith(_PROJECT_SUFFIXES):
                    continue
                try:
                    if not child.is_symlink() and child.is_file(
                        follow_symlinks=False
                    ):
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def _is_regular_file(path: Path) -> bool:
    try:
        if path.is_symlink():
            return False
        return path.is_file()
    except OSError:
        return False


def _is_broad_root(root: Path) -> bool:
    home = Path.home().resolve(strict=False)
    filesystem_root = Path(root.anchor).resolve(strict=False)
    return root == home or root == filesystem_root
