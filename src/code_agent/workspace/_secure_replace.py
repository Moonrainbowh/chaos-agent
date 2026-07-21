from __future__ import annotations

import hashlib
import os
import secrets
import stat
import tempfile
from pathlib import Path
from typing import BinaryIO, Mapping

from . import _posix_io
from . import _secure_io as safety
from ._secure_modes import (
    make_destination_writable,
    restore_destination_mode,
    restore_mode,
)
from ._secure_posix import identity_from_fd, inspect_at, open_verified_directory
from .errors import PathOutsideWorkspace, WorkspaceError
from .paths import WorkspacePathGuard


_TEMP_PREFIX = ".code-agent-edit-"


def secure_atomic_write(
    state: safety.TargetState,
    content: bytes,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
) -> None:
    safety.verify_target_state(state, guard, created, context="restore")
    try:
        if os.name == "posix":
            _atomic_write_posix(state, content, guard, created)
        else:
            _atomic_write_windows(state, content, guard, created)
    except (PathOutsideWorkspace, WorkspaceError):
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot atomically restore file: {state.target}") from error


def _atomic_write_posix(
    state: safety.TargetState,
    content: bytes,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
) -> None:
    parent_fd = open_verified_directory(
        state.target.parent, state.parent, guard, created, context="restore"
    )
    temporary_name: str | None = None
    temporary_identity: safety.PathIdentity | None = None
    descriptor: int | None = None
    temporary_created = False
    moved = False
    try:
        temporary_name, descriptor = _create_posix_temp(parent_fd)
        temporary_created = True
        temporary_identity = identity_from_fd(descriptor, state.target)
        _verify_visible_temp(state.target.parent / temporary_name, temporary_identity)
        owned_descriptor, descriptor = descriptor, None
        _write_descriptor(owned_descriptor, content, restore_mode(state))
        _verify_posix_target(parent_fd, state, guard, created)
        _verify_posix_temp(parent_fd, temporary_name, temporary_identity)
        _posix_io.replace(parent_fd, temporary_name, state.target.name)
        moved = True
        _verify_posix_result(parent_fd, state.target.name, temporary_identity, content)
    finally:
        cleanup_error: WorkspaceError | None = None
        if not moved and temporary_created and temporary_name is not None:
            try:
                _remove_posix_temp(parent_fd, temporary_name, temporary_identity)
            except WorkspaceError as error:
                cleanup_error = error
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)
        if cleanup_error is not None:
            raise cleanup_error


def _atomic_write_windows(
    state: safety.TargetState,
    content: bytes,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
) -> None:
    """Replace after static reparse rejection; native NT handles are out of scope."""
    temporary_path: Path | None = None
    temporary_identity: safety.PathIdentity | None = None
    destination_changed = False
    temporary_created = False
    moved = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=_TEMP_PREFIX, dir=state.target.parent, delete=False
        ) as stream:
            temporary_path = Path(stream.name)
            temporary_created = True
            temporary_identity = identity_from_fd(stream.fileno(), temporary_path)
            _verify_visible_temp(temporary_path, temporary_identity)
            _write_stream(stream, content)
        os.chmod(temporary_path, restore_mode(state))
        safety.verify_target_state(state, guard, created, context="restore")
        _verify_visible_temp(temporary_path, temporary_identity)
        destination_changed = make_destination_writable(state)
        os.replace(temporary_path, state.target)
        moved = True
        _verify_path_result(state.target, temporary_identity, content, guard)
    finally:
        if destination_changed and not moved:
            restore_destination_mode(state)
        if temporary_path is not None and temporary_created and not moved:
            _remove_path_temp(
                temporary_path,
                temporary_identity,
                state,
                guard,
                created,
            )


def _create_posix_temp(parent_fd: int) -> tuple[str, int]:
    for _ in range(32):
        name = f"{_TEMP_PREFIX}{secrets.token_hex(8)}"
        try:
            return name, _posix_io.create_temp(parent_fd, name)
        except FileExistsError:
            continue
    raise WorkspaceError("cannot allocate a unique restore temporary file")


def _write_descriptor(fd: int, content: bytes, mode: int) -> None:
    with os.fdopen(fd, "wb") as stream:
        _write_stream(stream, content)
        os.fchmod(stream.fileno(), mode)


def _write_stream(stream: BinaryIO, content: bytes) -> None:
    stream.write(content)
    stream.flush()
    os.fsync(stream.fileno())


def _verify_posix_target(
    parent_fd: int,
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
) -> None:
    safety.verify_target_state(state, guard, created, context="restore")
    current = inspect_at(parent_fd, state.target.name, missing_ok=True, context="restore")
    if current != state.identity:
        raise WorkspaceError(f"path changed during restore: {state.target}")


def _verify_posix_temp(
    parent_fd: int, name: str, expected: safety.PathIdentity
) -> None:
    current = inspect_at(parent_fd, name, missing_ok=False, context="restore")
    if current != expected:
        raise WorkspaceError(f"temporary path changed during restore: {name}")


def _verify_visible_temp(path: Path, expected: safety.PathIdentity) -> None:
    current = safety._inspect_path(path, missing_ok=False, context="restore")
    if current != expected:
        raise WorkspaceError(f"temporary path changed during restore: {path}")


def _verify_posix_result(
    parent_fd: int, name: str, expected: safety.PathIdentity, content: bytes
) -> None:
    descriptor = _posix_io.open_read(parent_fd, name)
    try:
        opened = identity_from_fd(descriptor, Path(name))
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            restored = stream.read(len(content) + 1)
            final = identity_from_fd(stream.fileno(), Path(name))
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    visible = inspect_at(parent_fd, name, missing_ok=False, context="restore")
    if opened != expected or final != expected or visible != expected:
        raise WorkspaceError(f"restored file changed after replace: {name}")
    _verify_result_content(opened, restored, content, name)


def _verify_path_result(
    path: Path,
    expected: safety.PathIdentity,
    content: bytes,
    guard: WorkspacePathGuard,
) -> None:
    guard.resolve(path, for_write=True)
    visible = safety._inspect_path(path, missing_ok=False, context="restore")
    try:
        with path.open("rb") as stream:
            opened = identity_from_fd(stream.fileno(), path)
            restored = stream.read(len(content) + 1)
            final = identity_from_fd(stream.fileno(), path)
    except OSError as error:
        raise WorkspaceError(f"restored file changed after replace: {path}") from error
    if visible != expected or opened != expected or final != expected:
        raise WorkspaceError(f"restored file changed after replace: {path}")
    _verify_result_content(opened, restored, content, str(path))


def _verify_result_content(
    identity: safety.PathIdentity, restored: bytes, expected: bytes, label: str
) -> None:
    content_matches = hashlib.sha256(restored).digest() == hashlib.sha256(expected).digest()
    if identity.size != len(expected) or len(restored) != len(expected) or not content_matches:
        raise WorkspaceError(f"restored file changed after replace: {label}")


def _remove_posix_temp(
    parent_fd: int, name: str, expected: safety.PathIdentity | None
) -> None:
    try:
        current = inspect_at(parent_fd, name, missing_ok=True, context="restore")
        if current is None:
            return
        if expected is not None and current != expected:
            raise WorkspaceError(f"temporary ownership changed during cleanup: {name}")
        descriptor = _posix_io.open_read(parent_fd, name)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        _posix_io.unlink(parent_fd, name)
        if inspect_at(parent_fd, name, missing_ok=True, context="restore") is not None:
            raise WorkspaceError(f"cannot clean restore temporary file: {name}")
    except WorkspaceError:
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot clean restore temporary file: {name}") from error


def _remove_path_temp(
    path: Path,
    expected: safety.PathIdentity | None,
    state: safety.TargetState,
    guard: WorkspacePathGuard,
    created: Mapping[str, tuple[Path, safety.PathIdentity]],
) -> None:
    if path.parent != state.target.parent or not path.name.startswith(_TEMP_PREFIX):
        raise WorkspaceError(f"invalid restore temporary path: {path}")
    safety.verify_parent_state(state.parent, guard, created, context="restore")
    try:
        current = _inspect_windows_temp(path)
        if current is None:
            return
        if expected is not None and current != expected:
            raise WorkspaceError(f"temporary ownership changed during cleanup: {path}")
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        path.unlink()
        if _inspect_windows_temp(path) is not None:
            raise WorkspaceError(f"cannot clean restore temporary file: {path}")
    except WorkspaceError:
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot clean restore temporary file: {path}") from error


def _inspect_windows_temp(path: Path) -> safety.PathIdentity | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(metadata.st_mode) or attributes & reparse:
        raise WorkspaceError(f"linked restore temporary path is protected: {path}")
    identity = safety.identity_from_stat(metadata)
    if not safety.is_regular(identity):
        raise WorkspaceError(f"restore temporary path is not a file: {path}")
    return identity
