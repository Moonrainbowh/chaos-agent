from __future__ import annotations

from pathlib import Path
from typing import Iterable, Protocol

from .errors import FileTooLargeError, WorkspaceError
from .paths import WorkspacePathGuard


class RestoreEntry(Protocol):
    relative_path: str
    content: bytes | None
    existed: bool


def preflight_restore(
    entries: Iterable[RestoreEntry],
    guard: WorkspacePathGuard,
    max_file_bytes: int,
) -> list[tuple[RestoreEntry, Path]]:
    """Resolve and validate a complete restore set before its first write."""
    resolved: list[tuple[RestoreEntry, Path]] = []
    seen: set[str] = set()
    for entry in entries:
        target = guard.resolve(entry.relative_path, for_write=True)
        relative = guard.relative(target).as_posix()
        if relative in seen:
            raise ValueError(f"duplicate restore path: {relative}")
        seen.add(relative)
        _check_blob(entry, target, max_file_bytes)
        _check_target(entry, target)
        resolved.append((entry, target))
    return resolved


def _check_blob(entry: RestoreEntry, target: Path, max_file_bytes: int) -> None:
    if entry.existed:
        if not isinstance(entry.content, bytes):
            raise TypeError(f"snapshot content must be bytes: {entry.relative_path}")
        if len(entry.content) > max_file_bytes:
            raise FileTooLargeError(
                f"file exceeds {max_file_bytes} bytes: {target}"
            )
    elif entry.content is not None:
        raise ValueError("missing snapshot entries cannot contain bytes")


def _check_target(entry: RestoreEntry, target: Path) -> None:
    if not target.parent.is_dir():
        raise WorkspaceError(f"parent directory does not exist: {target.parent}")
    if target.exists() and not target.is_file():
        raise WorkspaceError(f"snapshot path is not a file: {target}")
