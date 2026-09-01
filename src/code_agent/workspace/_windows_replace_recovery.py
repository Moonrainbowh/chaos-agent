from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Callable, Mapping

from . import _secure_io as safety
from ._secure_modes import restore_mode
from ._secure_temp import TEMP_PREFIX, remove_windows_temp
from ._windows_replace_native import (
    FILE_READ_ATTRIBUTES,
    close_handle,
    open_file_guard,
    replace_file,
    require_file_identity,
    set_handle_readonly,
)
from .errors import WorkspaceError
from .paths import WorkspacePathGuard


def reserve_backup(parent: Path) -> Path:
    """Choose an unobservable backup name; ReplaceFileW creates it atomically."""
    for _ in range(32):
        path = parent / f"{TEMP_PREFIX}{secrets.token_hex(6)}"
        current = safety._inspect_path(path, missing_ok=True, context="edit")
        if current is None:
            return path
    raise WorkspaceError("cannot allocate a unique replacement backup path")


def require_backup_identity(
    backup: Path, expected: safety.PathIdentity, handle: int
) -> None:
    require_file_identity(handle, backup, expected)
    if not path_has_identity(backup, expected):
        raise WorkspaceError("replacement backup identity changed")


def restore_displaced_winner(
    backup: Path,
    temporary_identity: safety.PathIdentity,
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
) -> None:
    """Put a concurrently installed target back after a detected name swap."""
    winner = safety._inspect_path(backup, missing_ok=False, context="edit")
    if winner is None or winner in {state.identity, temporary_identity}:
        raise WorkspaceError(
            f"cannot identify displaced concurrent file; preserved backup: {backup}"
        )
    if not path_has_identity(state.target, temporary_identity):
        raise WorkspaceError(
            f"edited target changed during recovery; preserved backup: {backup}"
    )
    winner_handle = open_file_guard(backup, FILE_READ_ATTRIBUTES)
    recovery: Path | None = None
    try:
        require_file_identity(winner_handle, backup, winner)
        recovery = reserve_backup(state.target.parent)
        try:
            replace_file(state.target, backup, recovery)
        except OSError as error:
            raise WorkspaceError(
                "cannot restore concurrently installed file; preserved recovery "
                f"files: {backup}, {recovery}"
            ) from error
        require_file_identity(winner_handle, state.target, winner)
        if not path_has_identity(recovery, temporary_identity):
            raise WorkspaceError(
                f"edited recovery file changed; preserved recovery: {recovery}"
            )
        remove_windows_temp(
            recovery, temporary_identity, state, guard, created
        )
    finally:
        close_handle(winner_handle)


def recover_partial_replace(
    code: int,
    temporary: Path,
    temporary_identity: safety.PathIdentity,
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
    validate: Callable[[], None] | None,
    backup: Path,
    target_handle: int,
    context: str,
) -> bool:
    assert state.identity is not None
    set_handle_readonly(
        target_handle, not bool(restore_mode(state) & 0o200)
    )
    if code == 1177:
        if state.target.exists() or not path_has_identity(backup, state.identity):
            return True
        if not path_has_identity(temporary, temporary_identity):
            return True
        try:
            os.rename(backup, state.target)
        except OSError:
            return True
        _validate_target(state, guard, created, validate, context)
        return False
    _validate_target(state, guard, created, validate, context)
    if not path_has_identity(temporary, temporary_identity):
        return True
    return path_has_identity(backup, state.identity)


def path_has_identity(path: Path, expected: safety.PathIdentity) -> bool:
    try:
        current = safety._inspect_path(path, missing_ok=True, context="edit")
    except WorkspaceError:
        return False
    return current is not None and current == expected


def _validate_target(
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
    validate: Callable[[], None] | None,
    context: str,
) -> None:
    if validate is not None:
        validate()
    safety.verify_target_state(state, guard, created, context=context)
