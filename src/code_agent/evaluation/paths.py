from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath


_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def checked_relative(value: str, name: str = "path") -> str:
    """Return a normalized relative path with Windows roots rejected everywhere."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{name} must contain relative paths")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        normalized.startswith(("/", "//"))
        or _WINDOWS_DRIVE.match(normalized)
        or path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or any(part in ("", ".") for part in path.parts)
    ):
        raise ValueError(f"{name} must contain relative paths")
    return path.as_posix()


def contained_path(root: Path, relative: str, name: str = "path") -> Path:
    """Resolve a materialization target and prove it remains below root."""
    checked = checked_relative(relative, name)
    resolved_root = root.resolve()
    lexical = resolved_root / checked
    candidate = lexical.resolve(strict=False)
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"{name} escapes its root") from error
    return lexical


def ensure_no_link_parent(root: Path, target: Path, name: str = "path") -> None:
    """Reject materialization through a pre-existing symlink or junction parent."""
    resolved_root = root.resolve()
    if (target.exists() or target.is_symlink()) and is_link_or_reparse(target):
        raise ValueError(f"{name} traverses a link")
    if target == resolved_root:
        return
    current = target.parent
    while current != resolved_root:
        if current.exists() and is_link_or_reparse(current):
            raise ValueError(f"{name} traverses a link")
        current = current.parent
        if resolved_root not in current.parents and current != resolved_root:
            raise ValueError(f"{name} escapes its root")


def is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    reparse = getattr(os, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse)
