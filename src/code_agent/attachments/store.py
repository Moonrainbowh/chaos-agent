from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path

from code_agent.core.attachments import AttachmentRef

from .errors import AttachmentError, AttachmentIntegrityError


class AttachmentStore:
    """Atomic SHA-256 blob storage whose public references contain no path."""

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be a Path")
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve(strict=True)
        _require_plain_directory(self._root)

    @property
    def root(self) -> Path:
        return self._root

    def put(
        self,
        data: bytes,
        *,
        media_type: str,
        display_name: str,
        width: int | None = None,
        height: int | None = None,
    ) -> AttachmentRef:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        digest = hashlib.sha256(data).hexdigest()
        reference = AttachmentRef(
            digest, media_type, len(data), display_name, width, height
        )
        directory = self._root / digest[:2]
        directory.mkdir(exist_ok=True)
        _require_plain_directory(directory)
        destination = self._blob_path(reference)
        if destination.exists():
            self.read(reference)
            return reference
        self._write_atomic(directory, destination, data)
        self.read(reference)
        return reference

    def read(self, reference: AttachmentRef) -> bytes:
        if not isinstance(reference, AttachmentRef):
            raise TypeError("reference must be an AttachmentRef")
        path = self._blob_path(reference)
        _require_plain_file(path)
        try:
            with path.open("rb") as stream:
                data = stream.read(reference.size_bytes + 1)
        except OSError:
            raise AttachmentIntegrityError("attachment blob cannot be read") from None
        if len(data) != reference.size_bytes:
            raise AttachmentIntegrityError("attachment blob size does not match reference")
        if hashlib.sha256(data).hexdigest() != reference.sha256:
            raise AttachmentIntegrityError("attachment blob digest does not match reference")
        return data

    def _blob_path(self, reference: AttachmentRef) -> Path:
        directory = self._root / reference.sha256[:2]
        path = directory / f"{reference.sha256}.blob"
        try:
            if path.parent.resolve(strict=False) != directory:
                raise AttachmentIntegrityError("attachment blob path is invalid")
            directory.relative_to(self._root)
        except (OSError, ValueError):
            raise AttachmentIntegrityError("attachment blob path escapes store") from None
        return path

    @staticmethod
    def _write_atomic(directory: Path, destination: Path, data: bytes) -> None:
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="xb", dir=directory, prefix=".pending-", delete=False
            ) as stream:
                temporary = stream.name
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            temporary = None
        except OSError:
            raise AttachmentError("attachment blob could not be stored") from None
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink()
                except OSError:
                    pass


def _require_plain_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise AttachmentIntegrityError("attachment store directory is unavailable") from None
    if not stat.S_ISDIR(metadata.st_mode) or _is_link_like(path, metadata):
        raise AttachmentIntegrityError("attachment store directory is not plain")


def _require_plain_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise AttachmentIntegrityError("attachment blob is missing") from None
    if not stat.S_ISREG(metadata.st_mode) or _is_link_like(path, metadata):
        raise AttachmentIntegrityError("attachment blob is not a plain file")


def _is_link_like(path: Path, metadata: os.stat_result) -> bool:
    if path.is_symlink():
        return True
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & flag)
