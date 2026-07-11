from __future__ import annotations

import ctypes
import os
from pathlib import Path
from types import TracebackType
from typing import Optional

from code_agent.workspace.paths import WorkspacePathGuard

from .errors import RuntimeStartError


_IS_WINDOWS = os.name == "nt"

_FILE_READ_ATTRIBUTES = 0x0080
# Metadata-only handles do not enforce delete sharing on current Windows builds.
_FILE_LIST_DIRECTORY = 0x0001
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_NAME_NORMALIZED = 0


if _IS_WINDOWS:
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _kernel32.CreateFileW
    _CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _CreateFileW.restype = wintypes.HANDLE

    _GetFinalPathNameByHandleW = _kernel32.GetFinalPathNameByHandleW
    _GetFinalPathNameByHandleW.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    _GetFinalPathNameByHandleW.restype = wintypes.DWORD

    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = (wintypes.HANDLE,)
    _CloseHandle.restype = wintypes.BOOL

    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
else:
    _INVALID_HANDLE_VALUE = None


class DirectoryLease:
    """Keep a validated working directory stable while a process is spawned."""

    def __init__(
        self,
        path: os.PathLike[str] | str,
        guard: WorkspacePathGuard,
    ) -> None:
        self._requested_path = path
        self._guard = guard
        self._handle: Optional[int] = None
        self._entered = False
        self.path = Path(path)

    @property
    def active(self) -> bool:
        return self._entered

    def __enter__(self) -> "DirectoryLease":
        if self._entered:
            raise RuntimeStartError("working-directory lease is already active")

        canonical = self._guard.resolve(self._requested_path)
        self.path = canonical
        if not _IS_WINDOWS:
            self._entered = True
            return self

        handle = _open_directory(canonical)
        try:
            final_path = Path(_normalize_final_path_name(_get_final_path_name(handle)))
            try:
                final_canonical = self._guard.resolve(final_path)
            except Exception as error:
                raise RuntimeStartError(
                    f"working directory resolved outside workspace: {canonical}"
                ) from error
            if not _same_path(final_canonical, canonical):
                raise RuntimeStartError(
                    f"working directory changed during startup: {canonical}"
                )
        except BaseException:
            try:
                _close_handle(handle)
            except OSError:
                pass
            raise

        self._handle = handle
        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        del exc_type, exc_value, traceback
        handle = self._handle
        self._handle = None
        self._entered = False
        if handle is None:
            return
        try:
            _close_handle(handle)
        except OSError as error:
            raise RuntimeStartError(
                "failed to release working-directory lease"
            ) from error


def _open_directory(path: Path) -> int:
    handle = _CreateFileW(
        str(path),
        _FILE_READ_ATTRIBUTES | _FILE_LIST_DIRECTORY,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        error = ctypes.WinError(ctypes.get_last_error())
        raise RuntimeStartError(f"failed to lease working directory: {path}") from error
    return handle


def _get_final_path_name(handle: int) -> str:
    size = 260
    while True:
        buffer = ctypes.create_unicode_buffer(size)
        length = _GetFinalPathNameByHandleW(
            handle,
            buffer,
            size,
            _FILE_NAME_NORMALIZED,
        )
        if length == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        if length < size:
            return buffer.value
        size = length + 1


def _close_handle(handle: int) -> None:
    if not _CloseHandle(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _normalize_final_path_name(path: str) -> str:
    folded = path.casefold()
    if folded.startswith("\\\\?\\unc\\"):
        return "\\\\" + path[8:]
    if folded.startswith("\\\\?\\"):
        return path[4:]
    return path


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(
        os.path.normpath(right)
    )
