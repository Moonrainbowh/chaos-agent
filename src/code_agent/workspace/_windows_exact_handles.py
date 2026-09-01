from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes
from pathlib import Path

from ._windows_guarded_open import (
    _OPEN_REPARSE_POINT,
    _WindowsOpenFailure,
    _close_all,
    _close_handle,
    _create_handle,
    _final_handle_path,
    _open_parent,
)
from ._windows_replace_native import file_identity
from ._secure_io import PathIdentity
from .errors import BatchEditConflictError, FileTooLargeError, WorkspaceError
from .paths import WorkspacePathGuard


GENERIC_READ = 0x80000000
DELETE = 0x00010000
FILE_READ_ATTRIBUTES = 0x80
SHARE_READ = 0x1


def open_locked_source(
    path: Path,
    expected_sha256: str,
    expected_size: int,
    max_bytes: int,
    expected_identity: PathIdentity | None = None,
) -> int:
    try:
        handle = _create_handle(
            path,
            desired_access=GENERIC_READ | DELETE | FILE_READ_ATTRIBUTES,
            share_mode=SHARE_READ,
            flags=_OPEN_REPARSE_POINT,
        )
    except _WindowsOpenFailure as error:
        raise ctypes.WinError(error.error_code) from error
    try:
        final = _final_handle_path(handle)
        if os.path.normcase(str(final)) != os.path.normcase(str(path)):
            raise WorkspaceError(f"move source resolved elsewhere: {path}")
        if final.name != path.name:
            raise BatchEditConflictError(
                f"move source exact name changed: {path.name}"
            )
        if expected_identity is not None and file_identity(handle) != (
            expected_identity.device,
            expected_identity.inode,
        ):
            raise BatchEditConflictError(
                f"move source identity changed: {path}"
            )
        content = read_handle(handle, max_bytes)
        if len(content) != expected_size or _sha256(content) != expected_sha256:
            raise BatchEditConflictError(f"move source content changed: {path}")
    except BaseException:
        _close_handle(handle)
        raise
    return handle


def open_protected_parents(
    source: Path,
    destination: Path,
    guard: WorkspacePathGuard,
) -> tuple[list[int], int]:
    handles: list[int] = []
    by_path: dict[str, int] = {}
    try:
        for leaf in (source, destination):
            for parent in _workspace_parent_paths(leaf, guard.root):
                key = os.path.normcase(str(parent))
                if key in by_path:
                    continue
                expected = guard.root_identity if parent == guard.root else None
                handle = _open_parent(parent, expected)
                handles.append(handle)
                by_path[key] = handle
        destination_parent = by_path[os.path.normcase(str(destination.parent))]
        return handles, destination_parent
    except BaseException:
        _close_all(handles)
        raise


def _workspace_parent_paths(leaf: Path, root: Path) -> tuple[Path, ...]:
    relative = leaf.relative_to(root)
    current = root
    parents = [root]
    for part in relative.parts[:-1]:
        current /= part
        parents.append(current)
    return tuple(parents)


def close_handles(
    source_handle: int | None,
    parent_handles: list[int],
) -> BaseException | None:
    first: BaseException | None = None
    if source_handle is not None:
        try:
            _close_handle(source_handle)
        except BaseException as error:
            first = error
    parent_error = _close_all(parent_handles)
    return first or parent_error


def read_handle(handle: int, max_bytes: int) -> bytes:
    _seek_start(handle)
    function = ctypes.WinDLL("kernel32", use_last_error=True).ReadFile
    function.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    function.restype = wintypes.BOOL
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining:
        requested = min(remaining, 64 * 1024)
        buffer = ctypes.create_string_buffer(requested)
        read = wintypes.DWORD()
        if not function(handle, buffer, requested, ctypes.byref(read), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if read.value == 0:
            break
        chunks.append(buffer.raw[: read.value])
        remaining -= read.value
    content = b"".join(chunks)
    if len(content) > max_bytes:
        raise FileTooLargeError(f"file exceeds {max_bytes} bytes")
    return content


def source_volume(handle: int) -> int:
    return file_identity(handle)[0]


def _seek_start(handle: int) -> None:
    function = ctypes.WinDLL("kernel32", use_last_error=True).SetFilePointerEx
    function.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int64,
        ctypes.POINTER(ctypes.c_int64),
        wintypes.DWORD,
    )
    function.restype = wintypes.BOOL
    if not function(handle, 0, None, 0):
        raise ctypes.WinError(ctypes.get_last_error())


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
