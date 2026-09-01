from __future__ import annotations

import os
import stat
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from . import _posix_io
from . import _secure_io as safety
from ._secure_modes import make_destination_writable, restore_destination_mode
from ._secure_posix import identity_from_fd, inspect_at, open_verified_directory
from .errors import PathOutsideWorkspace, WorkspaceError
from .paths import WorkspacePathGuard
from ._windows_file_locks import (
    DEFAULT_WINDOWS_FILE_LOCK_TIMEOUT_S,
    DELETE_RETRY_WINERRORS,
    retry_windows_file_operation,
)


CreatedDirectories = dict[str, tuple[Path, safety.PathIdentity]]


def ensure_parent_directories(
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: CreatedDirectories,
) -> safety.TargetState:
    for directory in state.parent.missing:
        key = safety.canonical_path_key(directory)
        if key in created:
            continue
        safety.verify_parent_state(state.parent, guard, created, context="restore")
        identity = _create_directory(directory, state, guard, created)
        created[key] = (directory, identity)
        safety.verify_parent_state(state.parent, guard, created, context="restore")
    refreshed = safety._capture_parent_state(
        state.target.parent, guard, context="restore"
    )
    return replace(state, parent=refreshed)


def secure_unlink(
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
    timeout_s: float = DEFAULT_WINDOWS_FILE_LOCK_TIMEOUT_S,
) -> None:
    _secure_remove(state, guard, created, directory=False, timeout_s=timeout_s)


def secure_rmdir(
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
    timeout_s: float = DEFAULT_WINDOWS_FILE_LOCK_TIMEOUT_S,
) -> None:
    _secure_remove(state, guard, created, directory=True, timeout_s=timeout_s)


def _create_directory(
    directory: Path,
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: CreatedDirectories,
) -> safety.PathIdentity:
    try:
        if os.name == "posix":
            identity = _create_directory_posix(directory, state, guard, created)
        else:
            directory.mkdir()
            visible = safety._inspect_path(directory, missing_ok=False, context="restore")
            assert visible is not None
            identity = visible
    except (PathOutsideWorkspace, WorkspaceError):
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot create restore parent: {directory}") from error
    if not stat.S_ISDIR(identity.mode):
        raise WorkspaceError(f"restore parent is not a directory: {directory}")
    return identity


def _create_directory_posix(
    directory: Path,
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: CreatedDirectories,
) -> safety.PathIdentity:
    parent_fd = open_verified_directory(
        directory.parent, state.parent, guard, created, context="restore"
    )
    child_fd: int | None = None
    try:
        _posix_io.mkdir(parent_fd, directory.name)
        child_fd = _posix_io.open_directory_at(parent_fd, directory.name)
        identity = identity_from_fd(child_fd, directory)
        visible = safety._inspect_path(directory, missing_ok=False, context="restore")
        if visible != identity:
            raise WorkspaceError(f"parent changed during restore: {directory}")
        return identity
    finally:
        if child_fd is not None:
            os.close(child_fd)
        os.close(parent_fd)


def _secure_remove(
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
    *,
    directory: bool,
    timeout_s: float,
) -> None:
    safety.verify_target_state(state, guard, created, context="restore")
    try:
        if os.name == "posix":
            _remove_posix(state, guard, created, directory=directory)
        else:
            _remove_windows(
                state, guard, created, directory=directory, timeout_s=timeout_s
            )
    except (PathOutsideWorkspace, WorkspaceError):
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot remove restored path: {state.target}") from error
    safety.verify_parent_state(state.parent, guard, created, context="restore")
    if safety._inspect_path(state.target, missing_ok=True, context="restore") is not None:
        raise WorkspaceError(f"path changed during restore: {state.target}")


def _remove_posix(
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
    *,
    directory: bool,
) -> None:
    parent_fd = open_verified_directory(
        state.target.parent, state.parent, guard, created, context="restore"
    )
    try:
        current = inspect_at(parent_fd, state.target.name, missing_ok=False, context="restore")
        if not safety.same_path_state(current, state.identity):
            raise WorkspaceError(f"path changed during restore: {state.target}")
        if directory:
            _posix_io.rmdir(parent_fd, state.target.name)
        else:
            _posix_io.unlink(parent_fd, state.target.name)
    finally:
        os.close(parent_fd)


def _remove_windows(
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
    *,
    directory: bool,
    timeout_s: float,
) -> None:
    def remove_attempt() -> None:
        changed = False if directory else make_destination_writable(state)
        try:
            state.target.rmdir() if directory else state.target.unlink()
        except OSError:
            if changed:
                restore_destination_mode(state)
            raise

    retry_windows_file_operation(
        remove_attempt,
        target=state.target,
        operation="remove restored path",
        timeout_s=timeout_s,
        retry_winerrors=DELETE_RETRY_WINERRORS,
        validate=lambda: safety.verify_target_state(
            state, guard, created, context="restore"
        ),
    )
