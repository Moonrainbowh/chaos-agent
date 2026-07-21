from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Callable

from ._secure_io import PathIdentity, identity_from_stat, is_regular
from .errors import WorkspaceError


_TEMP_PREFIX = ".tmp-"
_READ_CHUNK_BYTES = 65_536


class BlobIntegrityFailure(WorkspaceError):
    """Internal integrity failure translated by the public store API."""


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
    target = blob_path(blobs_root, digest)
    _prepare_shard(blobs_root, target.parent)
    if _path_exists(target):
        read_blob(target, digest, len(content), "existing blob")
        return
    temporary: Path | None = None
    temporary_identity: PathIdentity | None = None
    primary: BaseException | None = None
    moved = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=_TEMP_PREFIX, dir=target.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            temporary_identity = identity_from_stat(os.fstat(stream.fileno()))
            stream.write(content)
            stream.flush()
            fsync(stream.fileno())
        read_blob(temporary, digest, len(content), "temporary blob")
        if _path_exists(target):
            read_blob(target, digest, len(content), "concurrent blob")
            return
        try:
            replace(temporary, target)
            moved = True
        except OSError:
            if _valid_concurrent_blob(target, digest, len(content)):
                return
            raise
        read_blob(target, digest, len(content), "published blob")
    except BaseException as error:
        primary = error
        raise
    finally:
        if temporary is not None and not moved:
            _cleanup_temp(temporary, temporary_identity, primary)


def read_blob(path: Path, digest: str, size: int, label: str) -> bytes:
    expected = _inspect_regular(path, label)
    if expected.size != size:
        raise BlobIntegrityFailure(f"{label} size does not match manifest")
    try:
        with path.open("rb") as stream:
            opened = identity_from_stat(os.fstat(stream.fileno()))
            content = _read_limited(stream, size)
            final = identity_from_stat(os.fstat(stream.fileno()))
    except BlobIntegrityFailure:
        raise
    except OSError as error:
        raise BlobIntegrityFailure(f"cannot read {label}: {path}") from error
    visible = _inspect_regular(path, label)
    if opened != expected or final != expected or visible != expected:
        raise BlobIntegrityFailure(f"{label} changed while being read")
    if len(content) != size:
        raise BlobIntegrityFailure(f"{label} size does not match manifest")
    if hashlib.sha256(content).hexdigest() != digest:
        raise BlobIntegrityFailure(f"{label} digest does not match its name")
    return content


def inspect_blob(path: Path, label: str) -> PathIdentity:
    return _inspect_regular(path, label)


def _prepare_shard(blobs_root: Path, shard: Path) -> None:
    try:
        blobs_root.mkdir(parents=True, exist_ok=True)
        _require_directory(blobs_root, "blob root")
        shard.mkdir(exist_ok=True)
        _require_directory(shard, "blob shard")
    except BlobIntegrityFailure:
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot create blob shard: {shard}") from error


def _require_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise BlobIntegrityFailure(f"cannot inspect {label}: {path}") from error
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise BlobIntegrityFailure(f"{label} is not a real directory: {path}")
    if attributes & reparse:
        raise BlobIntegrityFailure(f"{label} is a reparse point: {path}")


def _inspect_regular(path: Path, label: str) -> PathIdentity:
    try:
        identity = identity_from_stat(path.lstat())
    except FileNotFoundError as error:
        raise BlobIntegrityFailure(f"missing {label}: {path}") from error
    except OSError as error:
        raise BlobIntegrityFailure(f"cannot inspect {label}: {path}") from error
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if not is_regular(identity) or stat.S_ISLNK(identity.mode):
        raise BlobIntegrityFailure(f"{label} is not a regular blob: {path}")
    if identity.attributes & reparse:
        raise BlobIntegrityFailure(f"{label} is not a regular blob: {path}")
    return identity


def _read_limited(stream: object, size: int) -> bytes:
    content = bytearray()
    while len(content) <= size:
        remaining = size + 1 - len(content)
        chunk = stream.read(min(_READ_CHUNK_BYTES, remaining))
        if not chunk:
            break
        content.extend(chunk)
    return bytes(content)


def _valid_concurrent_blob(path: Path, digest: str, size: int) -> bool:
    try:
        read_blob(path, digest, size, "concurrent blob")
    except BlobIntegrityFailure:
        return False
    return True


def _cleanup_temp(
    path: Path, expected: PathIdentity | None, primary: BaseException | None
) -> None:
    try:
        if not _path_exists(path):
            return
        current = _inspect_regular(path, "temporary blob")
        if expected is None or current != expected:
            raise WorkspaceError(f"cleanup ownership failure: {path}")
        path.unlink()
    except WorkspaceError as error:
        _attach_cleanup(primary, error)
    except OSError as error:
        cleanup = WorkspaceError(f"cannot clean temporary blob: {path}")
        cleanup.__cause__ = error
        _attach_cleanup(primary, cleanup)


def _attach_cleanup(primary: BaseException | None, cleanup: WorkspaceError) -> None:
    if primary is None:
        raise cleanup
    setattr(primary, "cleanup_error", cleanup)
    add_note = getattr(primary, "add_note", None)
    if callable(add_note):
        add_note(f"blob cleanup failure: {cleanup}")


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise BlobIntegrityFailure(f"cannot inspect blob path: {path}") from error
    return True
