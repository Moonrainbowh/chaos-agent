from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
from pathlib import Path
from typing import BinaryIO, Callable

from . import _posix_io
from ._secure_io import PathIdentity, identity_from_stat
from ._snapshot_store_dirs import (
    BlobIntegrityFailure,
    StoreDirectory,
    close_directory,
    inspect_regular,
    open_shard,
    open_store_root,
    verify_chain,
)
from .errors import WorkspaceError


_TEMP_PREFIX = ".tmp-"
_READ_CHUNK_BYTES = 65_536


def blob_path(blobs_root: Path, digest: str) -> Path:
    return blobs_root / digest[:2] / digest


def publish_blob(
    blobs_root: Path,
    digest: str,
    content: bytes,
    *,
    replace: Callable[[object, object], None],
    fsync: Callable[[int], None],
) -> None:
    root = open_store_root(blobs_root, create=True)
    assert root is not None
    shard: StoreDirectory | None = None
    try:
        shard = open_shard(root, digest[:2], create=True)
        assert shard is not None
        _publish_in_shard(root, shard, digest, content, replace, fsync)
    finally:
        close_directory(shard)
        close_directory(root)


def read_blob(path: Path, digest: str, size: int, label: str) -> bytes:
    root = open_store_root(path.parent.parent, create=False)
    if root is None:
        raise BlobIntegrityFailure(f"missing {label}: {path}")
    shard: StoreDirectory | None = None
    try:
        shard = open_shard(root, path.parent.name, create=False)
        if shard is None:
            raise BlobIntegrityFailure(f"missing {label}: {path}")
        return _read_entry(root, shard, path.name, digest, size, label)
    finally:
        close_directory(shard)
        close_directory(root)


def _publish_in_shard(
    root: StoreDirectory,
    shard: StoreDirectory,
    digest: str,
    content: bytes,
    replace: Callable[[object, object], None],
    fsync: Callable[[int], None],
) -> None:
    if inspect_regular(shard, digest, "existing blob", missing_ok=True) is not None:
        _read_entry(root, shard, digest, digest, len(content), "existing blob")
        return
    temporary: str | None = None
    identity: PathIdentity | None = None
    primary: BaseException | None = None
    moved = False
    try:
        temporary, identity = _write_temp(root, shard, content, fsync)
        _read_entry(root, shard, temporary, digest, len(content), "temporary blob")
        if inspect_regular(shard, digest, "concurrent blob", missing_ok=True):
            _read_entry(root, shard, digest, digest, len(content), "concurrent blob")
            return
        try:
            _replace_temp(root, shard, temporary, digest, replace)
            moved = True
        except OSError:
            if _valid_concurrent_blob(root, shard, digest, len(content)):
                return
            raise
        _read_entry(root, shard, digest, digest, len(content), "published blob")
    except BaseException as error:
        primary = error
        raise
    finally:
        if temporary is not None and not moved:
            _cleanup_temp(root, shard, temporary, identity, primary)


def _write_temp(
    root: StoreDirectory,
    shard: StoreDirectory,
    content: bytes,
    fsync: Callable[[int], None],
) -> tuple[str, PathIdentity]:
    verify_chain(root, shard)
    name: str | None = None
    identity: PathIdentity | None = None
    created = False
    primary: BaseException | None = None
    try:
        if shard.descriptor is not None:
            name, descriptor = _create_posix_temp(shard.descriptor)
            created = True
            identity = identity_from_stat(os.fstat(descriptor))
            with os.fdopen(descriptor, "wb") as stream:
                _write_stream(stream, content, fsync)
        else:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=_TEMP_PREFIX, dir=shard.path, delete=False
            ) as stream:
                name = Path(stream.name).name
                created = True
                identity = identity_from_stat(os.fstat(stream.fileno()))
                _write_stream(stream, content, fsync)
        verify_chain(root, shard)
        visible = inspect_regular(shard, name, "temporary blob")
        if visible != identity:
            raise BlobIntegrityFailure(
                f"temporary blob changed: {shard.path / name}"
            )
        return name, identity
    except BaseException as error:
        primary = error
        raise
    finally:
        if created and primary is not None and name is not None:
            _cleanup_temp(root, shard, name, identity, primary)


def _read_entry(
    root: StoreDirectory,
    shard: StoreDirectory,
    name: str,
    digest: str,
    size: int,
    label: str,
) -> bytes:
    expected = inspect_regular(shard, name, label)
    assert expected is not None
    if expected.size != size:
        raise BlobIntegrityFailure(f"{label} size does not match manifest")
    verify_chain(root, shard)
    try:
        with _open_stream(shard, name) as stream:
            opened = identity_from_stat(os.fstat(stream.fileno()))
            verify_chain(root, shard)
            content = _read_limited(stream, size)
            final = identity_from_stat(os.fstat(stream.fileno()))
    except BlobIntegrityFailure:
        raise
    except OSError as error:
        raise BlobIntegrityFailure(f"cannot read {label}: {shard.path / name}") from error
    visible = inspect_regular(shard, name, label)
    verify_chain(root, shard)
    if opened != expected or final != expected or visible != expected:
        raise BlobIntegrityFailure(f"{label} changed while being read")
    _verify_content(content, digest, size, label)
    return content


def _replace_temp(
    root: StoreDirectory,
    shard: StoreDirectory,
    temporary: str,
    digest: str,
    replace: Callable[[object, object], None],
) -> None:
    verify_chain(root, shard)
    if shard.descriptor is not None:
        _posix_io.replace(shard.descriptor, temporary, digest)
    else:
        replace(shard.path / temporary, shard.path / digest)
    verify_chain(root, shard)


def _cleanup_temp(
    root: StoreDirectory,
    shard: StoreDirectory,
    name: str,
    expected: PathIdentity | None,
    primary: BaseException | None,
) -> None:
    try:
        current = inspect_regular(shard, name, "temporary blob", missing_ok=True)
        if current is None:
            return
        if expected is None or current != expected:
            raise WorkspaceError(f"cleanup ownership failure: {shard.path / name}")
        verify_chain(root, shard)
        if shard.descriptor is not None:
            _posix_io.unlink(shard.descriptor, name)
        else:
            (shard.path / name).unlink()
        verify_chain(root, shard)
        if inspect_regular(shard, name, "temporary blob", missing_ok=True) is not None:
            raise WorkspaceError(f"cannot clean temporary blob: {shard.path / name}")
    except WorkspaceError as error:
        _attach_cleanup(primary, error)
    except OSError as error:
        cleanup = WorkspaceError(f"cannot clean temporary blob: {shard.path / name}")
        cleanup.__cause__ = error
        _attach_cleanup(primary, cleanup)


def _create_posix_temp(parent_fd: int) -> tuple[str, int]:
    for _ in range(32):
        name = f"{_TEMP_PREFIX}{secrets.token_hex(8)}"
        try:
            return name, _posix_io.create_temp(parent_fd, name)
        except FileExistsError:
            continue
    raise WorkspaceError("cannot allocate a unique blob temporary file")


def _open_stream(shard: StoreDirectory, name: str) -> BinaryIO:
    if shard.descriptor is None:
        return (shard.path / name).open("rb")
    return os.fdopen(_posix_io.open_read(shard.descriptor, name), "rb")


def _write_stream(
    stream: BinaryIO, content: bytes, fsync: Callable[[int], None]
) -> None:
    stream.write(content)
    stream.flush()
    fsync(stream.fileno())


def _read_limited(stream: BinaryIO, size: int) -> bytes:
    content = bytearray()
    while len(content) <= size:
        remaining = size + 1 - len(content)
        chunk = stream.read(min(_READ_CHUNK_BYTES, remaining))
        if not chunk:
            break
        content.extend(chunk)
    return bytes(content)


def _verify_content(content: bytes, digest: str, size: int, label: str) -> None:
    if len(content) != size:
        raise BlobIntegrityFailure(f"{label} size does not match manifest")
    if hashlib.sha256(content).hexdigest() != digest:
        raise BlobIntegrityFailure(f"{label} digest does not match its name")


def _valid_concurrent_blob(
    root: StoreDirectory, shard: StoreDirectory, digest: str, size: int
) -> bool:
    try:
        _read_entry(root, shard, digest, digest, size, "concurrent blob")
    except BlobIntegrityFailure:
        return False
    return True


def _attach_cleanup(primary: BaseException | None, cleanup: WorkspaceError) -> None:
    if primary is None:
        raise cleanup
    setattr(primary, "cleanup_error", cleanup)
    add_note = getattr(primary, "add_note", None)
    if callable(add_note):
        add_note(f"blob cleanup failure: {cleanup}")
