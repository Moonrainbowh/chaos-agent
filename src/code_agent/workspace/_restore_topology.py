from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import _secure_io as safety
from .errors import WorkspaceError, WorkspaceScanLimitError
from .ignore import IgnoreRules
from .paths import WorkspacePathGuard


class RestoreEntry(Protocol):
    relative_path: str
    content: bytes | None
    existed: bool


@dataclass(frozen=True)
class TopologyItem:
    entry: RestoreEntry
    state: safety.TargetState


@dataclass(frozen=True)
class TopologyPlan:
    deletes: tuple[TopologyItem, ...]
    directories: tuple[safety.TargetState, ...]
    writes: tuple[TopologyItem, ...]


def analyze_topology(
    entries: tuple[RestoreEntry, ...], guard: WorkspacePathGuard
) -> TopologyPlan:
    rules = IgnoreRules.from_workspace(guard.root)
    tombstone_keys = {
        safety.canonical_path_key(entry.relative_path)
        for entry in entries
        if not entry.existed
    }
    _validate_target_tree(entries)
    deletes = _capture_deletes(entries, guard, rules)
    delete_by_key = {
        safety.canonical_path_key(item.entry.relative_path): item
        for item in deletes
    }
    directories: dict[str, safety.TargetState] = {}
    writes = [
        TopologyItem(
            entry,
            _capture_write_state(
                entry, guard, rules, tombstone_keys, delete_by_key, directories
            ),
        )
        for entry in entries
        if entry.existed
    ]
    return TopologyPlan(
        tuple(
            sorted(
                deletes,
                key=lambda item: _depth(item.entry.relative_path),
                reverse=True,
            )
        ),
        tuple(
            sorted(
                directories.values(),
                key=lambda state: _depth(state.target),
                reverse=True,
            )
        ),
        tuple(sorted(writes, key=lambda item: _depth(item.entry.relative_path))),
    )


def _capture_deletes(
    entries: tuple[RestoreEntry, ...],
    guard: WorkspacePathGuard,
    rules: IgnoreRules,
) -> list[TopologyItem]:
    output: list[TopologyItem] = []
    for entry in entries:
        if entry.existed:
            continue
        target = guard.resolve(entry.relative_path, for_write=True)
        relative = target.relative_to(guard.root).as_posix()
        if rules.is_ignored(relative):
            raise WorkspaceError(f"ignored restore path is protected: {relative}")
        state = safety.capture_target_state(target, guard, context="restore")
        if state.identity is None:
            continue
        if not safety.is_regular(state.identity):
            raise WorkspaceError(f"tombstone path is not a file: {target}")
        output.append(TopologyItem(entry, state))
    return output


def _capture_write_state(
    entry: RestoreEntry,
    guard: WorkspacePathGuard,
    rules: IgnoreRules,
    tombstones: set[str],
    delete_by_key: dict[str, TopologyItem],
    directories: dict[str, safety.TargetState],
) -> safety.TargetState:
    target = guard.resolve(entry.relative_path, for_write=True)
    relative = target.relative_to(guard.root).as_posix()
    if rules.is_ignored(relative):
        raise WorkspaceError(f"ignored restore path is protected: {relative}")
    blocker = _find_parent_blocker(target, guard)
    if blocker is not None:
        key = safety.canonical_path_key(blocker.relative_to(guard.root))
        if key not in delete_by_key:
            raise WorkspaceError(f"blocking path is not planned for deletion: {blocker}")
        return _future_state(target, delete_by_key[key].state)
    current = safety.capture_target_state(target, guard, context="restore")
    if current.identity is None or safety.is_regular(current.identity):
        return current
    if not stat.S_ISDIR(current.identity.mode):
        raise WorkspaceError(f"snapshot path is not a file: {target}")
    _scan_replaced_directory(
        target, guard, rules, tombstones, directories, [0]
    )
    return safety.TargetState(target, current.parent, None)


def _find_parent_blocker(
    target: Path, guard: WorkspacePathGuard
) -> Path | None:
    current = guard.root
    for part in target.parent.relative_to(guard.root).parts:
        current /= part
        identity = safety._inspect_path(
            current, missing_ok=True, context="restore"
        )
        if identity is None:
            return None
        if safety.is_regular(identity):
            return current
        if not stat.S_ISDIR(identity.mode):
            raise WorkspaceError(f"restore parent is not a directory: {current}")
    return None


def _future_state(
    target: Path, blocker: safety.TargetState
) -> safety.TargetState:
    missing: list[Path] = []
    current = blocker.target.parent
    for part in target.parent.relative_to(current).parts:
        current /= part
        missing.append(current)
    parent = safety.ParentState(
        target.parent, blocker.parent.existing, tuple(missing)
    )
    return safety.TargetState(target, parent, None)


def _scan_replaced_directory(
    directory: Path,
    guard: WorkspacePathGuard,
    rules: IgnoreRules,
    tombstones: set[str],
    directories: dict[str, safety.TargetState],
    visited: list[int],
) -> None:
    visited[0] += 1
    if visited[0] > max(1_024, len(tombstones) * 2 + 16):
        raise WorkspaceScanLimitError("restore topology scan exceeded its limit")
    try:
        children = tuple(os.scandir(directory))
    except OSError as error:
        raise WorkspaceError(f"cannot inspect restore directory: {directory}") from error
    for child in children:
        path = Path(child.path)
        relative = path.relative_to(guard.root).as_posix()
        try:
            checked = guard.resolve(path, for_write=True)
        except WorkspaceError as error:
            raise WorkspaceError(
                f"unplanned directory content blocks restore: {relative}"
            ) from error
        if rules.is_ignored(relative):
            raise WorkspaceError(
                f"unplanned directory content blocks restore: {relative}"
            )
        identity = safety._inspect_path(
            checked, missing_ok=False, context="restore"
        )
        assert identity is not None
        if stat.S_ISDIR(identity.mode):
            _scan_replaced_directory(
                checked, guard, rules, tombstones, directories, visited
            )
        elif (
            not safety.is_regular(identity)
            or safety.canonical_path_key(relative) not in tombstones
        ):
            raise WorkspaceError(
                f"unplanned directory content blocks restore: {relative}"
            )
    state = safety.capture_target_state(directory, guard, context="restore")
    directories[safety.canonical_path_key(directory)] = state


def _validate_target_tree(entries: tuple[RestoreEntry, ...]) -> None:
    existing = [
        entry.relative_path.replace("\\", "/").strip("/")
        for entry in entries
        if entry.existed
    ]
    keys = {path: safety.canonical_path_key(path) for path in existing}
    for path in existing:
        prefix = keys[path] + "/"
        if any(
            other != path and keys[other].startswith(prefix)
            for other in existing
        ):
            raise WorkspaceError(f"target file contains another target: {path}")


def _depth(value: str | os.PathLike[str]) -> int:
    return len(Path(value).parts)
