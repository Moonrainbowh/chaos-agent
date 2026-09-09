from __future__ import annotations

import os
import re
from pathlib import PurePosixPath


_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


def canonical_repo_path(path: str) -> str:
    """Return one validated workspace-relative POSIX repository path."""
    if not isinstance(path, str):
        raise TypeError("path must be text")
    if not path or "\0" in path:
        raise ValueError("path must be non-empty text without NUL")
    if "\\" in path or path.startswith("/") or _DRIVE_PREFIX.match(path):
        raise ValueError("path must be workspace-relative POSIX text")
    if "//" in path or path.endswith("/"):
        raise ValueError("path must already be canonical POSIX text")
    parts = tuple(path.split("/"))
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("path must not contain dot segments")
    canonical = PurePosixPath(*parts).as_posix()
    if canonical in {"", "."}:
        raise ValueError("path must identify a repository entry")
    return canonical


def canonical_path_key(
    path: str, *, case_insensitive: bool | None = None
) -> str:
    """Return a stable comparison key while display spelling stays separate."""
    canonical = canonical_repo_path(path)
    insensitive = os.name == "nt" if case_insensitive is None else case_insensitive
    if not isinstance(insensitive, bool):
        raise TypeError("case_insensitive must be a boolean or None")
    return canonical.casefold() if insensitive else canonical


def logical_lines(text: str) -> tuple[str, ...]:
    """Split decoded text into logical lines independently of newline style."""
    if not isinstance(text, str):
        raise TypeError("text must be text")
    return tuple(text.splitlines())
