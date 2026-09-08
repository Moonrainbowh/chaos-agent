from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import BinaryIO, Callable

from . import _posix_io
from ._secure_io import (
    PathIdentity,
    TargetState,
    identity_from_stat,
    is_regular,
    verify_target_state,
)
from ._secure_posix import open_verified_directory
from .errors import FileTooLargeError, PathOutsideWorkspace, WorkspaceError
from .paths import WorkspacePathGuard


_READ_CHUNK_BYTES = 65_536


def secure_read_bytes(
    state: TargetState,
    guard: WorkspacePathGuard,
    max_bytes: int,
    check_deadline: Callable[[], None],
) -> tuple[bytes, int]:
    if not is_regular(state.identity):
        raise WorkspaceError(f"not a regular file: {state.target}")
    assert state.identity is not None
    if state.identity.size > max_bytes:
        raise FileTooLargeError(f"file exceeds {max_bytes} bytes: {state.target}")
    verify_target_state(state, guard, {}, context="inventory")
    try:
        with _open_stream(state, guard) as stream:
            _verify_open_handle(stream, state, guard)
            content = _read_chunks(stream, max_bytes, check_deadline, state.target)
            _verify_open_handle(stream, state, guard)
    except (FileTooLargeError, PathOutsideWorkspace, WorkspaceError):
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot read inventory file: {state.target}") from error
    return content, stat.S_IMODE(state.identity.mode)


def _open_stream(state: TargetState, guard: WorkspacePathGuard) -> BinaryIO:
    if os.name != "posix":
        return state.target.open("rb")
    parent_fd = open_verified_directory(
        state.target.parent, state.parent, guard, {}, context="inventory"
    )
    try:
        descriptor = _posix_io.open_read(parent_fd, state.target.name)
    finally:
        os.close(parent_fd)
    return os.fdopen(descriptor, "rb")


def _verify_open_handle(
    stream: BinaryIO, state: TargetState, guard: WorkspacePathGuard
) -> None:
    try:
        metadata = os.fstat(stream.fileno())
    except OSError as error:
        raise WorkspaceError(f"cannot inspect open file: {state.target}") from error
    opened = identity_from_stat(metadata)
    if opened != state.identity or not _same_metadata(opened, state.identity):
        raise WorkspaceError(f"path changed during inventory: {state.target}")
    verify_target_state(state, guard, {}, context="inventory")


def _same_metadata(current: PathIdentity, expected: PathIdentity) -> bool:
    if os.name == "nt":
        # On Windows, os.stat sets synthetic 0o111 execute bits for .exe/.bat/.cmd files,
        # but os.fstat(fileno) cannot determine the extension and returns 0o666.
        # Compare file type and read/write permission bits.
        mode_match = (
            stat.S_IFMT(current.mode) == stat.S_IFMT(expected.mode)
            and (current.mode & 0o600) == (expected.mode & 0o600)
        )
    else:
        mode_match = current.mode == expected.mode
    return (
        mode_match
        and current.size == expected.size
        and current.modified_ns == expected.modified_ns
    )


def _read_chunks(
    stream: BinaryIO,
    max_bytes: int,
    check_deadline: Callable[[], None],
    target: Path,
) -> bytes:
    content = bytearray()
    while len(content) <= max_bytes:
        check_deadline()
        remaining = max_bytes + 1 - len(content)
        chunk = stream.read(min(_READ_CHUNK_BYTES, remaining))
        check_deadline()
        if not chunk:
            break
        content.extend(chunk)
    if len(content) > max_bytes:
        raise FileTooLargeError(f"file exceeds {max_bytes} bytes: {target}")
    return bytes(content)
