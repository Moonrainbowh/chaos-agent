from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from ._restore_topology import TopologyPlan, analyze_topology
from ._secure_io import (
    PathIdentity,
    canonical_path_key,
    refresh_directory_after_owned_mutations,
)
from ._secure_mutation import (
    ensure_parent_directories,
    secure_rmdir,
    secure_unlink,
)
from ._secure_replace import secure_atomic_write
from .errors import FileTooLargeError, WorkspaceError
from .paths import WorkspacePathGuard
from ._windows_file_locks import DEFAULT_WINDOWS_FILE_LOCK_TIMEOUT_S


class RestoreEntry(Protocol):
    relative_path: str
    content: bytes | None
    existed: bool


@dataclass(frozen=True)
class RestorePlan:
    topology: TopologyPlan
    total_bytes: int


def preflight_restore(
    entries: Iterable[RestoreEntry],
    guard: WorkspacePathGuard,
    max_file_bytes: int,
    max_total_bytes: int,
) -> RestorePlan:
    """Validate the complete restore set before its first mutation."""
    supplied = tuple(entries)
    seen: set[str] = set()
    total = 0
    for entry in supplied:
        target = guard.resolve(entry.relative_path, for_write=True)
        relative = target.relative_to(guard.root).as_posix()
        key = canonical_path_key(relative)
        if key in seen:
            raise ValueError(f"duplicate restore path: {relative}")
        seen.add(key)
        total += _check_blob(entry, target, max_file_bytes)
    if total > max_total_bytes:
        raise FileTooLargeError(
            f"restore exceeds {max_total_bytes} total bytes"
        )
    topology = analyze_topology(supplied, guard)
    _check_capacity(guard.root, total)
    for state in _mutating_states(topology):
        _check_write_access(state.parent.nearest_existing)
    return RestorePlan(topology, total)


def execute_restore(
    plan: RestorePlan,
    guard: WorkspacePathGuard,
    *,
    file_lock_timeout_s: float = DEFAULT_WINDOWS_FILE_LOCK_TIMEOUT_S,
) -> None:
    """Apply a dependency-ordered plan with per-operation revalidation."""
    created: dict[str, tuple[Path, PathIdentity]] = {}
    for item in plan.topology.deletes:
        secure_unlink(item.state, guard, created, file_lock_timeout_s)
    for state in plan.topology.directories:
        state = refresh_directory_after_owned_mutations(
            state, guard, created, context="restore"
        )
        secure_rmdir(state, guard, created, file_lock_timeout_s)
    for item in plan.topology.writes:
        assert item.entry.content is not None
        state = ensure_parent_directories(item.state, guard, created)
        secure_atomic_write(
            state,
            item.entry.content,
            guard,
            created,
            timeout_s=file_lock_timeout_s,
        )


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


def _mutating_states(topology: TopologyPlan):
    yield from (item.state for item in topology.deletes)
    yield from topology.directories
    yield from (item.state for item in topology.writes)


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


def _check_write_access(ancestor: Path) -> None:
    if not os.access(ancestor, os.W_OK):
        raise WorkspaceError(f"restore parent is not writable: {ancestor}")
