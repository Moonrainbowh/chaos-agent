from __future__ import annotations

import os
import posixpath
import stat
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from .errors import PathOutsideWorkspace, WorkspaceError
from .paths import WorkspacePathGuard


@dataclass(frozen=True)
class PathIdentity:
    device: int
    inode: int
    mode: int
    attributes: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class ParentState:
    parent: Path
    existing: tuple[tuple[Path, PathIdentity], ...]
    missing: tuple[Path, ...]

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
    return os.path.normcase(normalized).casefold()


def capture_target_state(
    target: Path, guard: WorkspacePathGuard, *, context: str
) -> TargetState:
    checked = guard.resolve(target, for_write=True)
    parent = _capture_parent_state(checked.parent, guard, context=context)
    identity = _inspect_path(checked, missing_ok=True, context=context)
    return TargetState(checked, parent, identity)


def is_regular(identity: PathIdentity | None) -> bool:
    return identity is not None and stat.S_ISREG(identity.mode)


def ensure_parent_directories(
    state: TargetState,
    guard: WorkspacePathGuard,
    created: dict[str, tuple[Path, PathIdentity]],
) -> TargetState:
    for directory in state.parent.missing:
        key = canonical_path_key(directory)
        if key in created:
            continue
        verify_parent_state(state.parent, guard, created, context="restore")
        try:
            directory.mkdir()
        except OSError as error:
            raise WorkspaceError(f"cannot create restore parent: {directory}") from error
        checked = guard.resolve(directory, for_write=True)
        identity = _inspect_path(checked, missing_ok=False, context="restore")
        assert identity is not None
        if not stat.S_ISDIR(identity.mode):
            raise WorkspaceError(f"restore parent is not a directory: {directory}")
        created[key] = (checked, identity)
        verify_parent_state(state.parent, guard, created, context="restore")
    refreshed = _capture_parent_state(state.target.parent, guard, context="restore")
    return replace(state, parent=refreshed)


def secure_atomic_write(
    state: TargetState,
    content: bytes,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, PathIdentity]],
) -> None:
    verify_target_state(state, guard, created, context="restore")
    temporary_path: Path | None = None
    temporary_identity: PathIdentity | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".code-agent-edit-", dir=state.target.parent, delete=False
        ) as stream:
            temporary_path = Path(stream.name)
            verify_parent_state(state.parent, guard, created, context="restore")
            temporary_identity = _inspect_path(
                temporary_path, missing_ok=False, context="restore"
            )
            _verify_temp_handle(stream.fileno(), temporary_identity, temporary_path)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _verify_owned_temp(temporary_path, temporary_identity)
        if state.identity is not None:
            os.chmod(temporary_path, stat.S_IMODE(state.identity.mode))
        verify_target_state(state, guard, created, context="restore")
        _verify_owned_temp(temporary_path, temporary_identity)
        os.replace(temporary_path, state.target)
        temporary_path = None
        verify_parent_state(state.parent, guard, created, context="restore")
        restored = _inspect_path(state.target, missing_ok=False, context="restore")
        if not is_regular(restored):
            raise WorkspaceError(f"restored path is not a file: {state.target}")
    except (PathOutsideWorkspace, WorkspaceError):
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot atomically restore file: {state.target}") from error
    finally:
        if temporary_path is not None and temporary_identity is not None:
            _remove_owned_temp(temporary_path, temporary_identity)


def secure_unlink(
    state: TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, PathIdentity]],
) -> None:
    verify_target_state(state, guard, created, context="restore")
    try:
        state.target.unlink()
    except OSError as error:
        raise WorkspaceError(f"cannot remove restored path: {state.target}") from error
    verify_parent_state(state.parent, guard, created, context="restore")
    guard.resolve(state.target, for_write=True)
    if _inspect_path(state.target, missing_ok=True, context="restore") is not None:
        raise WorkspaceError(f"path changed during restore: {state.target}")


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
    if current != state.identity:
        raise WorkspaceError(f"path changed during {context}: {state.target}")


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
    current = guard.root
    root_identity = _inspect_path(current, missing_ok=False, context=context)
    assert root_identity is not None
    existing.append((current, root_identity))
    for part in parent.relative_to(guard.root).parts:
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
    return PathIdentity(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        attributes,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _remove_owned_temp(path: Path, expected: PathIdentity) -> None:
    try:
        current = _inspect_path(path, missing_ok=True, context="restore")
        if current is not None and _same_object(current, expected):
            path.unlink()
    except (OSError, WorkspaceError):
        pass


def _verify_owned_temp(path: Path, expected: PathIdentity) -> None:
    current = _inspect_path(path, missing_ok=False, context="restore")
    if current is None or not _same_object(current, expected):
        raise WorkspaceError(f"temporary path changed during restore: {path}")


def _verify_temp_handle(fd: int, expected: PathIdentity, path: Path) -> None:
    try:
        metadata = os.fstat(fd)
    except OSError as error:
        raise WorkspaceError(f"cannot inspect restore temp: {path}") from error
    current = PathIdentity(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        getattr(metadata, "st_file_attributes", 0),
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    if not _same_object(current, expected):
        raise WorkspaceError(f"temporary path changed during restore: {path}")


def _same_object(current: PathIdentity, expected: PathIdentity) -> bool:
    return (
        current.device,
        current.inode,
        current.mode,
        current.attributes,
    ) == (
        expected.device,
        expected.inode,
        expected.mode,
        expected.attributes,
    )
