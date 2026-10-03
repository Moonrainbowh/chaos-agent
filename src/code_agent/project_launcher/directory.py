"""Shallow, bounded directory navigation for selecting a project on a phone."""
from __future__ import annotations

import os
from pathlib import Path

_IGNORED = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv"}


def browse_roots(projects: tuple[Path, ...]) -> tuple[Path, ...]:
    """Offer known projects, home and available local drive anchors, without scanning."""
    candidates = list(projects) + [Path.home()]
    if os.name == "nt":
        candidates += [Path(letter + ":/") for letter in "CDEF"]
    else:
        candidates.append(Path("/"))
    result = {}
    for root in candidates:
        if root.is_absolute() and root.is_dir():
            root = root.resolve(strict=False)
            result.setdefault(os.path.normcase(str(root)), root)
    return tuple(result.values())


def child_directories(root: Path) -> tuple[Path, ...]:
    """Read at most 1000 child directories; never recursively inventory a drive."""
    found = []
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.name.casefold() in _IGNORED:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    found.append(Path(entry.path))
            except OSError:
                continue
            if len(found) >= 1000:
                break
    return tuple(sorted(found, key=lambda path: path.name.casefold()))
