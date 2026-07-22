from __future__ import annotations

import os
import stat
from pathlib import Path

from .errors import WorkspaceError


def require_contained_unlinked(storage_root: Path, target: Path) -> None:
    """Require a literal target and each existing component to remain unlinked."""
    absolute = Path(os.path.abspath(target))
    try:
        relative = absolute.relative_to(storage_root)
        absolute.resolve(strict=False).relative_to(storage_root)
    except (OSError, RuntimeError, ValueError) as error:
        raise WorkspaceError("managed worktree target escapes storage root") from error
    current = storage_root
    for part in relative.parts:
        current /= part
        if is_link_like(current):
            raise WorkspaceError(f"linked managed worktree path is not allowed: {current}")


def is_link_like(path: Path) -> bool:
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
