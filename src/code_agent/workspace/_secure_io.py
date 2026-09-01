from __future__ import annotations

import os
import posixpath
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping

from .errors import PathOutsideWorkspace, WorkspaceError
from .paths import WorkspacePathGuard


@dataclass(frozen=True)
class PathIdentity:
    device: int
    inode: int
    mode: int = field(compare=False)
    attributes: int = field(compare=False)
    size: int = field(compare=False)
    modified_ns: int = field(compare=False)


@dataclass(frozen=True)
class ParentState:
    parent: Path
    existing: tuple[tuple[Path, PathIdentity], ...]
    missing: tuple[Path, ...]

    @property
    def anchor(self) -> Path:
        return self.existing[0][0]

    @property
    def nearest_existing(self) -> Path:
        return self.existing[-1][0]


@dataclass(frozen=True)
class TargetState:
    target: Path
    parent: ParentState
    identity: PathIdentity | None


def canonical_path_key(value: str | os.PathLike[str]) -> str:
    normalized = posixpath.normpath(os.fspath(value).replace("\\", "/"))
    return normalized.casefold() if os.name == "nt" else normalized


def capture_target_state(
    target: Path, guard: WorkspacePathGuard, *, context: str
) -> TargetState:
    checked = guard.resolve(target, for_write=True)
    parent = _capture_parent_state(checked.parent, guard, context=context)
    identity = _inspect_path(checked, missing_ok=True, context=context)
    return TargetState(checked, parent, identity)


def is_regular(identity: PathIdentity | None) -> bool:
    return identity is not None and stat.S_ISREG(identity.mode)


def verify_target_state(
    state: TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, PathIdentity]],
    *,
    context: str,
) -> None:
    verify_parent_state(state.parent, guard, created, context=context)
    checked = guard.resolve(state.target, for_write=True)
    current = _inspect_path(checked, missing_ok=True, context=context)
    if not same_path_state(current, state.identity):
        raise WorkspaceError(f"path changed during {context}: {state.target}")


def refresh_directory_after_owned_mutations(
    state: TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, PathIdentity]],
    *,
    context: str,
) -> TargetState:
    """Accept expected child changes without accepting a replaced directory."""
    verify_parent_state(state.parent, guard, created, context=context)
    checked = guard.resolve(state.target, for_write=True)
    current = _inspect_path(checked, missing_ok=False, context=context)
    if not _same_directory_object(current, state.identity):
        raise WorkspaceError(f"path changed during {context}: {state.target}")
    return replace(state, identity=current)


def verify_parent_state(
    state: ParentState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, PathIdentity]],
    *,
    context: str,
) -> None:
    guard.resolve(state.parent, for_write=True)
    for path, expected in state.existing:
        current = _inspect_path(path, missing_ok=False, context=context)
        if current is None or not _same_object(current, expected):
            raise WorkspaceError(f"parent changed during {context}: {path}")
    for path in state.missing:
        owned = created.get(canonical_path_key(path))
        if owned is not None:
            current = _inspect_path(path, missing_ok=False, context=context)
            if current is None or not _same_object(current, owned[1]):
                raise WorkspaceError(f"parent changed during {context}: {path}")
        elif _inspect_path(path, missing_ok=True, context=context) is not None:
            raise WorkspaceError(f"parent changed during {context}: {path}")


def _capture_parent_state(
    parent: Path, guard: WorkspacePathGuard, *, context: str
) -> ParentState:
    guard.resolve(parent, for_write=True)
    existing: list[tuple[Path, PathIdentity]] = []
    missing: list[Path] = []
    try:
        relative = parent.relative_to(guard.root)
        current = guard.root
    except ValueError:
        current = Path(parent.anchor)
        relative = parent.relative_to(current)
    root_identity = _inspect_path(current, missing_ok=False, context=context)
    assert root_identity is not None
    existing.append((current, root_identity))
    for part in relative.parts:
        current /= part
        identity = _inspect_path(current, missing_ok=True, context=context)
        if identity is None:
            missing.append(current)
        elif missing:
            raise WorkspaceError(f"parent appeared below a missing path: {current}")
        elif not stat.S_ISDIR(identity.mode):
            raise WorkspaceError(f"restore parent is not a directory: {current}")
        else:
            existing.append((current, identity))
    return ParentState(parent, tuple(existing), tuple(missing))


def _inspect_path(
    path: Path, *, missing_ok: bool, context: str
) -> PathIdentity | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise WorkspaceError(f"path disappeared during {context}: {path}")
    except OSError as error:
        raise PathOutsideWorkspace(
            f"cannot inspect path metadata during {context}: {path}"
        ) from error
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(metadata.st_mode) or attributes & reparse:
        raise PathOutsideWorkspace(f"linked paths are not allowed: {path}")
    return identity_from_stat(metadata)


def identity_from_stat(metadata: os.stat_result) -> PathIdentity:
    return PathIdentity(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        getattr(metadata, "st_file_attributes", 0),
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _same_object(current: PathIdentity, expected: PathIdentity) -> bool:
    return current == expected


def _same_directory_object(
    current: PathIdentity | None, expected: PathIdentity | None
) -> bool:
    return bool(
        current is not None
        and expected is not None
        and current == expected
        and stat.S_ISDIR(current.mode)
        and stat.S_ISDIR(expected.mode)
        and current.mode == expected.mode
        and current.attributes == expected.attributes
    )


def same_path_state(
    current: PathIdentity | None, expected: PathIdentity | None
) -> bool:
    """Compare object identity plus mutation-relevant metadata."""
    if current is None or expected is None:
        return current is expected
    return (
        current == expected
        and current.mode == expected.mode
        and current.attributes == expected.attributes
        and current.size == expected.size
        and current.modified_ns == expected.modified_ns
    )
