from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Mapping

from . import _posix_io
from . import _secure_io as safety
from .errors import PathOutsideWorkspace, WorkspaceError
from .paths import WorkspacePathGuard


def open_verified_directory(
    directory: Path,
    state: safety.ParentState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
    *,
    context: str,
) -> int:
    """Open an expected directory chain without following path links."""
    safety.verify_parent_state(state, guard, created, context=context)
    descriptor: int | None = None
    try:
        descriptor = _posix_io.open_directory(state.anchor)
        _verify_fd(
            descriptor, _expected(state.anchor, state, created), state.anchor, context
        )
        current = state.anchor
        for part in directory.relative_to(state.anchor).parts:
            child = _posix_io.open_directory_at(descriptor, part)
            previous, descriptor = descriptor, child
            os.close(previous)
            current /= part
            _verify_fd(
                descriptor, _expected(current, state, created), current, context
            )
        return descriptor
    except (PathOutsideWorkspace, WorkspaceError):
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise WorkspaceError(f"cannot open directory during {context}: {directory}") from error


def inspect_at(
    parent_fd: int, name: str, *, missing_ok: bool, context: str
) -> safety.PathIdentity | None:
    try:
        metadata = _posix_io.inspect(parent_fd, name)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise WorkspaceError(f"path disappeared during {context}: {name}")
    except OSError as error:
        raise PathOutsideWorkspace(
            f"cannot inspect path metadata during {context}: {name}"
        ) from error
    if stat.S_ISLNK(metadata.st_mode):
        raise PathOutsideWorkspace(f"linked paths are not allowed: {name}")
    return safety.identity_from_stat(metadata)


def identity_from_fd(fd: int, target: Path) -> safety.PathIdentity:
    try:
        return safety.identity_from_stat(os.fstat(fd))
    except OSError as error:
        raise WorkspaceError(f"cannot inspect open file: {target}") from error


def _expected(
    path: Path,
    state: safety.ParentState,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
) -> safety.PathIdentity:
    expected = dict(state.existing).get(path)
    owned = created.get(safety.canonical_path_key(path))
    if owned is not None:
        expected = owned[1]
    if expected is None:
        raise WorkspaceError(f"directory was not present during preflight: {path}")
    return expected


def _verify_fd(
    fd: int, expected: safety.PathIdentity, path: Path, context: str
) -> None:
    current = identity_from_fd(fd, path)
    if current != expected or not stat.S_ISDIR(current.mode):
        raise WorkspaceError(f"parent changed during {context}: {path}")
