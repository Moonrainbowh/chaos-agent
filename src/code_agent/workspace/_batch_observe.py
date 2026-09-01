from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from ._batch_models import PlannedPathState
from ._secure_io import PathIdentity, capture_target_state, same_path_state
from .errors import BatchEditConflictError, WorkspaceError
from .paths import PathInput, WorkspacePathGuard


@dataclass(frozen=True)
class PathObservation:
    state: PlannedPathState
    content: bytes | None
    identity: PathIdentity | None


def literal_relative(guard: WorkspacePathGuard, path: PathInput) -> str:
    return WorkspacePathGuard.relative_literal(guard, path).as_posix()


def observe(editor: object, relative_path: str) -> PathObservation:
    guard = editor.guard
    target = guard.root / Path(relative_path)
    content, lookup_existed = editor._read_current(target, editor.max_file_bytes)
    if not lookup_existed:
        state = PlannedPathState(relative_path, False, None, 0, False, None, 0)
        return PathObservation(state, None, None)
    digest = _sha256(content)
    exact = _exact_leaf_exists(target)
    identity = capture_target_state(target, guard, context="batch observe").identity
    state = PlannedPathState(
        relative_path,
        exact,
        digest if exact else None,
        len(content) if exact else 0,
        True,
        digest,
        len(content),
    )
    return PathObservation(state, content, identity)


def require_state(
    editor: object,
    expected: PlannedPathState,
    *,
    require_identity: PathIdentity | None = None,
) -> PathObservation:
    current = observe(editor, expected.relative_path)
    if not same_public_state(current.state, expected):
        raise BatchEditConflictError(
            f"batch path drifted: {expected.relative_path}"
        )
    if require_identity is not None and not same_path_state(
        current.identity, require_identity
    ):
        raise BatchEditConflictError(
            f"batch path identity drifted: {expected.relative_path}"
        )
    return current


def same_public_state(left: PlannedPathState, right: PlannedPathState) -> bool:
    return (
        left.relative_path == right.relative_path
        and left.existed == right.existed
        and left.sha256 == right.sha256
        and left.size == right.size
        and left.lookup_existed == right.lookup_existed
        and left.lookup_sha256 == right.lookup_sha256
        and left.lookup_size == right.lookup_size
    )


def existing_state(relative_path: str, content: bytes) -> PlannedPathState:
    digest = _sha256(content)
    return PlannedPathState(
        relative_path, True, digest, len(content), True, digest, len(content)
    )


def missing_state(relative_path: str) -> PlannedPathState:
    return PlannedPathState(relative_path, False, None, 0, False, None, 0)


def alias_state(relative_path: str, content: bytes) -> PlannedPathState:
    digest = _sha256(content)
    return PlannedPathState(
        relative_path, False, None, 0, True, digest, len(content), True
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _exact_leaf_exists(path: Path) -> bool:
    if os.name != "nt":
        return path.exists()
    actual = _find_actual_leaf(path)
    return actual == path.name


def _find_actual_leaf(path: Path) -> str | None:
    class FindData(ctypes.Structure):
        _fields_ = (
            ("attributes", wintypes.DWORD),
            ("creation", wintypes.FILETIME),
            ("access", wintypes.FILETIME),
            ("write", wintypes.FILETIME),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("reserved0", wintypes.DWORD),
            ("reserved1", wintypes.DWORD),
            ("name", wintypes.WCHAR * 260),
            ("alternate", wintypes.WCHAR * 14),
        )

    data = FindData()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    find = kernel.FindFirstFileW
    find.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(FindData))
    find.restype = wintypes.HANDLE
    handle = find(str(path), ctypes.byref(data))
    if handle == wintypes.HANDLE(-1).value:
        code = ctypes.get_last_error()
        if code in (2, 3):
            return None
        raise WorkspaceError(f"cannot inspect exact Windows path: {path}")
    try:
        return str(data.name)
    finally:
        close = kernel.FindClose
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        close(handle)
