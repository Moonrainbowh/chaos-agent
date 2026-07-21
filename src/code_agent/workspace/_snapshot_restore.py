from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from ._secure_io import (
    PathIdentity,
    TargetState,
    canonical_path_key,
    capture_target_state,
    ensure_parent_directories,
    is_regular,
    secure_atomic_write,
    secure_unlink,
)
from .errors import FileTooLargeError, WorkspaceError
from .paths import WorkspacePathGuard


class RestoreEntry(Protocol):
    relative_path: str
    content: bytes | None
    existed: bool


@dataclass(frozen=True)
class RestoreItem:
    entry: RestoreEntry
    state: TargetState
    no_op: bool


@dataclass(frozen=True)
class RestorePlan:
    items: tuple[RestoreItem, ...]
    total_bytes: int


def preflight_restore(
    entries: Iterable[RestoreEntry],
    guard: WorkspacePathGuard,
    max_file_bytes: int,
    max_total_bytes: int,
) -> RestorePlan:
    """Validate the complete restore set before its first mutation."""
    items: list[RestoreItem] = []
    seen: set[str] = set()
    total = 0
    for entry in entries:
        target = guard.resolve(entry.relative_path, for_write=True)
        relative = target.relative_to(guard.root).as_posix()
        key = canonical_path_key(relative)
        if key in seen:
            raise ValueError(f"duplicate restore path: {relative}")
        seen.add(key)
        total += _check_blob(entry, target, max_file_bytes)
        state = capture_target_state(target, guard, context="restore")
        no_op = _check_target(entry, state)
        items.append(RestoreItem(entry, state, no_op))
    if total > max_total_bytes:
        raise FileTooLargeError(
            f"restore exceeds {max_total_bytes} total bytes"
        )
    _check_capacity(guard.root, total)
    for item in items:
        _check_write_access(item)
    return RestorePlan(tuple(items), total)


def execute_restore(plan: RestorePlan, guard: WorkspacePathGuard) -> None:
    """Apply a preflighted plan with per-operation identity revalidation."""
    created: dict[str, tuple[Path, PathIdentity]] = {}
    for item in plan.items:
        if item.no_op:
            continue
        if item.entry.existed:
            assert item.entry.content is not None
            state = ensure_parent_directories(item.state, guard, created)
            secure_atomic_write(state, item.entry.content, guard, created)
        else:
            secure_unlink(item.state, guard, created)


def _check_blob(entry: RestoreEntry, target: Path, max_file_bytes: int) -> int:
    if entry.existed:
        if not isinstance(entry.content, bytes):
            raise TypeError(f"snapshot content must be bytes: {entry.relative_path}")
        if len(entry.content) > max_file_bytes:
            raise FileTooLargeError(
                f"file exceeds {max_file_bytes} bytes: {target}"
            )
        return len(entry.content)
    if entry.content is not None:
        raise ValueError("missing snapshot entries cannot contain bytes")
    return 0


def _check_target(entry: RestoreEntry, state: TargetState) -> bool:
    if state.identity is not None and not is_regular(state.identity):
        raise WorkspaceError(f"snapshot path is not a file: {state.target}")
    return not entry.existed and state.identity is None


def _check_capacity(root: Path, total_bytes: int) -> None:
    if total_bytes == 0:
        return
    try:
        free = shutil.disk_usage(root).free
    except OSError as error:
        raise WorkspaceError(f"cannot inspect restore disk space: {root}") from error
    if free < total_bytes:
        raise WorkspaceError(
            f"insufficient disk space for {total_bytes} restore bytes"
        )


def _check_write_access(item: RestoreItem) -> None:
    if item.no_op:
        return
    ancestor = item.state.parent.nearest_existing
    if not os.access(ancestor, os.W_OK):
        raise WorkspaceError(f"restore parent is not writable: {ancestor}")
