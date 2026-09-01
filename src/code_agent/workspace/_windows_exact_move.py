from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from ._windows_artifact_handles import IoStatusBlock, handle_identity, nt_function
from ._windows_exact_handles import (
    close_handles,
    open_locked_source,
    open_protected_parents,
    source_volume,
)
from ._windows_guarded_open import _final_handle_path
from .errors import CrossVolumeMoveError, WorkspaceError
from .paths import WorkspacePathGuard
from ._secure_io import PathIdentity


def validate_same_volume(
    source: Path,
    destination: Path,
    guard: WorkspacePathGuard,
    *,
    expected_sha256: str,
    expected_size: int,
    max_bytes: int,
    validate: Callable[[], None],
    expected_identity: PathIdentity,
) -> None:
    parent_handles: list[int] = []
    source_handle: int | None = None
    primary: BaseException | None = None
    try:
        validate()
        parent_handles, destination_parent = open_protected_parents(
            source, destination, guard
        )
        source_handle = open_locked_source(
            source, expected_sha256, expected_size, max_bytes, expected_identity
        )
        source_id, destination_id = _volume_ids(source_handle, destination_parent)
        if source_id != destination_id:
            raise _cross_volume_error(source, destination)
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup = close_handles(source_handle, parent_handles)
        if cleanup is not None:
            if primary is None:
                raise cleanup
            setattr(primary, "cleanup_error", cleanup)


def move_no_replace(
    source: Path,
    destination: Path,
    guard: WorkspacePathGuard,
    *,
    expected_sha256: str,
    expected_size: int,
    max_bytes: int,
    validate: Callable[[], None],
    expected_identity: PathIdentity,
) -> None:
    parent_handles: list[int] = []
    source_handle: int | None = None
    primary: BaseException | None = None
    committed = False
    try:
        validate()
        parent_handles, destination_parent = open_protected_parents(
            source, destination, guard
        )
        source_handle = open_locked_source(
            source, expected_sha256, expected_size, max_bytes, expected_identity
        )
        source_id, destination_id = _volume_ids(source_handle, destination_parent)
        if source_id != destination_id:
            raise _cross_volume_error(source, destination)
        try:
            _rename_no_replace(source_handle, destination_parent, destination.name)
        except OSError as error:
            if getattr(error, "winerror", None) == 17:
                raise _cross_volume_error(source, destination) from error
            raise
        committed = True
        final = _final_handle_path(source_handle)
        if (
            os.path.normcase(str(final)) != os.path.normcase(str(destination))
            or final.name != destination.name
        ):
            raise WorkspaceError(
                f"Windows move result has the wrong exact path: {destination}"
            )
    except BaseException as error:
        primary = error
        if committed:
            setattr(error, "publication_committed", True)
        raise
    finally:
        cleanup = close_handles(source_handle, parent_handles)
        if cleanup is not None:
            if primary is None:
                if committed:
                    setattr(cleanup, "publication_committed", True)
                raise cleanup
            setattr(primary, "cleanup_error", cleanup)


def _volume_ids(source_handle: int, destination_parent: int) -> tuple[int, int]:
    return source_volume(source_handle), handle_identity(destination_parent)[0]


def _cross_volume_error(source: Path, destination: Path) -> CrossVolumeMoveError:
    return CrossVolumeMoveError(
        f"exact move cannot cross volumes: {source} -> {destination}"
    )


def _rename_no_replace(
    source_handle: int,
    destination_parent: int,
    destination_name: str,
) -> None:
    payload = _rename_payload(destination_parent, destination_name)
    status_block = IoStatusBlock()
    function = nt_function("NtSetInformationFile")
    function.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
    )
    function.restype = wintypes.LONG
    status = function(
        source_handle, ctypes.byref(status_block), payload, len(payload), 10
    )
    if status < 0:
        converter = nt_function("RtlNtStatusToDosError")
        converter.argtypes = (wintypes.LONG,)
        converter.restype = wintypes.ULONG
        raise ctypes.WinError(converter(status))


def _rename_payload(
    destination_parent: int, destination_name: str
) -> ctypes.Array[ctypes.c_char]:
    class RenameInformation(ctypes.Structure):
        _fields_ = (
            ("replace", ctypes.c_ubyte),
            ("root", wintypes.HANDLE),
            ("length", wintypes.ULONG),
            ("name", wintypes.WCHAR * 1),
        )

    encoded = destination_name.encode("utf-16-le")
    offset = RenameInformation.name.offset
    payload = ctypes.create_string_buffer(
        max(ctypes.sizeof(RenameInformation), offset + len(encoded))
    )
    header = RenameInformation.from_buffer(payload)
    header.replace = 0
    header.root = destination_parent
    header.length = len(encoded)
    ctypes.memmove(ctypes.addressof(payload) + offset, encoded, len(encoded))
    return payload
