from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from . import _posix_io
from ._secure_io import PathIdentity, identity_from_stat, is_regular
from .errors import WorkspaceError


class BlobIntegrityFailure(WorkspaceError):
    """Internal integrity failure translated by the public store API."""


@dataclass(frozen=True)
class StoreDirectory:
    path: Path
    identity: PathIdentity
    descriptor: int | None = None


def open_store_root(path: Path, *, create: bool) -> StoreDirectory | None:
    if create:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise BlobIntegrityFailure(f"cannot create blob root: {path}") from error
    try:
        identity = _directory_identity(path, "blob root")
    except FileNotFoundError:
        if not create:
            return None
        raise
    handle = _open_directory(path, identity, "blob root")
    try:
        verify_directory(handle, "blob root")
    except BaseException:
        close_directory(handle)
        raise
    return handle


def open_shard(
    root: StoreDirectory, name: str, *, create: bool
) -> StoreDirectory | None:
    verify_directory(root, "blob root")
    path = root.path / name
    if create:
        _mkdir_shard(root, name, path)
    try:
        identity = _directory_identity(path, "blob shard")
    except FileNotFoundError:
        if not create:
            return None
        raise
    handle = _open_shard_directory(root, name, path, identity)
    try:
        verify_directory(root, "blob root")
        verify_directory(handle, "blob shard")
    except BaseException:
        close_directory(handle)
        raise
    return handle


def verify_directory(directory: StoreDirectory, label: str) -> None:
    try:
        visible = _directory_identity(directory.path, label)
    except FileNotFoundError as error:
        raise BlobIntegrityFailure(
            f"{label} disappeared: {directory.path}"
        ) from error
    if visible != directory.identity:
        raise BlobIntegrityFailure(f"{label} changed: {directory.path}")
    if directory.descriptor is None:
        return
    try:
        opened = identity_from_stat(os.fstat(directory.descriptor))
    except OSError as error:
        raise BlobIntegrityFailure(
            f"cannot inspect open {label}: {directory.path}"
        ) from error
    if opened != directory.identity or not stat.S_ISDIR(opened.mode):
        raise BlobIntegrityFailure(f"open {label} changed: {directory.path}")


def verify_chain(root: StoreDirectory, shard: StoreDirectory) -> None:
    verify_directory(root, "blob root")
    verify_directory(shard, "blob shard")


def inspect_regular(
    directory: StoreDirectory,
    name: str,
    label: str,
    *,
    missing_ok: bool = False,
) -> PathIdentity | None:
    verify_directory(directory, "blob shard")
    try:
        metadata = _inspect(directory, name)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise BlobIntegrityFailure(f"missing {label}: {directory.path / name}")
    except OSError as error:
        raise BlobIntegrityFailure(
            f"cannot inspect {label}: {directory.path / name}"
        ) from error
    identity = identity_from_stat(metadata)
    if not is_regular(identity) or _is_link_or_reparse(identity):
        raise BlobIntegrityFailure(
            f"{label} is not a regular blob: {directory.path / name}"
        )
    verify_directory(directory, "blob shard")
    return identity


def scan_target(directory: StoreDirectory) -> int | Path:
    verify_directory(directory, "blob directory")
    return directory.descriptor if directory.descriptor is not None else directory.path


def close_directory(directory: StoreDirectory | None) -> None:
    if directory is not None and directory.descriptor is not None:
        os.close(directory.descriptor)


def _mkdir_shard(root: StoreDirectory, name: str, path: Path) -> None:
    try:
        if root.descriptor is None:
            path.mkdir(exist_ok=True)
        else:
            try:
                _posix_io.mkdir(root.descriptor, name)
            except FileExistsError:
                pass
    except OSError as error:
        raise BlobIntegrityFailure(f"cannot create blob shard: {path}") from error
    verify_directory(root, "blob root")


def _open_directory(
    path: Path, expected: PathIdentity, label: str
) -> StoreDirectory:
    if os.name != "posix":
        return StoreDirectory(path, expected)
    descriptor: int | None = None
    try:
        descriptor = _posix_io.open_directory(path)
        opened = identity_from_stat(os.fstat(descriptor))
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise BlobIntegrityFailure(f"cannot open {label}: {path}") from error
    if opened != expected or not stat.S_ISDIR(opened.mode):
        os.close(descriptor)
        raise BlobIntegrityFailure(f"{label} changed before open: {path}")
    return StoreDirectory(path, expected, descriptor)


def _open_shard_directory(
    root: StoreDirectory, name: str, path: Path, expected: PathIdentity
) -> StoreDirectory:
    if root.descriptor is None:
        return StoreDirectory(path, expected)
    descriptor: int | None = None
    try:
        descriptor = _posix_io.open_directory_at(root.descriptor, name)
        opened = identity_from_stat(os.fstat(descriptor))
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise BlobIntegrityFailure(f"cannot open blob shard: {path}") from error
    if opened != expected or not stat.S_ISDIR(opened.mode):
        os.close(descriptor)
        raise BlobIntegrityFailure(f"blob shard changed before open: {path}")
    return StoreDirectory(path, expected, descriptor)


def _directory_identity(path: Path, label: str) -> PathIdentity:
    try:
        identity = identity_from_stat(path.lstat())
    except FileNotFoundError:
        raise
    except OSError as error:
        raise BlobIntegrityFailure(f"cannot inspect {label}: {path}") from error
    if not stat.S_ISDIR(identity.mode) or _is_link_or_reparse(identity):
        raise BlobIntegrityFailure(f"{label} is not a real directory: {path}")
    return identity


def _inspect(directory: StoreDirectory, name: str) -> os.stat_result:
    if directory.descriptor is not None:
        return _posix_io.inspect(directory.descriptor, name)
    return (directory.path / name).lstat()


def _is_link_or_reparse(identity: PathIdentity) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(identity.mode) or bool(identity.attributes & reparse)
