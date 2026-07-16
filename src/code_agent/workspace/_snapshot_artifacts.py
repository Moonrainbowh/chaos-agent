from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import uuid
from pathlib import Path

from ._guarded_read import read_guarded_file
from .errors import FileTooLargeError, WorkspaceError
from .paths import PathInput, WorkspacePathGuard


class SnapshotArtifacts:
    """Own the two fixed product-state artifact directories."""

    def __init__(self, product_state_root: PathInput, workspace_root: Path) -> None:
        self.root = _prepare_root(product_state_root, workspace_root)
        self.guard = WorkspacePathGuard(self.root, allow_sensitive=True)

    def new_identifier(self) -> str:
        for _ in range(8):
            identifier = uuid.uuid4().hex
            if not self._path(f"manifests/{identifier}.json").exists():
                return identifier
        raise WorkspaceError("cannot allocate a unique snapshot identifier")

    def read_manifest(self, identifier: str, limit: int) -> bytes:
        return self._read(f"manifests/{identifier}.json", limit)

    def write_manifest(self, identifier: str, content: bytes) -> None:
        _atomic_write(self._path(f"manifests/{identifier}.json"), content)

    def read_blob(self, digest: str, limit: int) -> bytes:
        return self._read(f"blobs/{digest}", limit)

    def ensure_blob(self, digest: str, content: bytes) -> None:
        relative = f"blobs/{digest}"
        target = self._path(relative)
        if target.exists():
            stored = self._read(relative, len(content))
            if stored != content or _sha256(stored) != digest:
                raise WorkspaceError("content-addressed snapshot blob is corrupt")
            return
        _atomic_write(target, content)

    def _read(self, relative: str, limit: int) -> bytes:
        content = read_guarded_file(relative, self.guard, limit)
        if len(content) > limit:
            raise FileTooLargeError(f"snapshot artifact exceeds {limit} bytes")
        return content

    def _path(self, relative: str) -> Path:
        return self.guard.resolve(relative, for_write=True)


def _prepare_root(value: PathInput, workspace: Path) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str):
        raise TypeError("product-state root must be text")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError("product-state root must be absolute")
    if _is_link_like(candidate):
        raise WorkspaceError("product-state root cannot be a link")
    root = candidate.resolve(strict=False)
    if _is_within(root, workspace):
        raise ValueError("product-state root must be outside the workspace")
    try:
        root.mkdir(parents=True, exist_ok=True)
        for name in ("manifests", "blobs"):
            directory = root / name
            directory.mkdir(exist_ok=True)
            if not directory.is_dir() or _is_link_like(directory):
                raise WorkspaceError("snapshot artifact directory is unsafe")
    except WorkspaceError:
        raise
    except OSError as error:
        raise WorkspaceError("cannot prepare snapshot artifact root") from error
    return root


def _atomic_write(target: Path, content: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".snapshot-", dir=target.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    except OSError as error:
        raise WorkspaceError(f"cannot atomically write snapshot artifact: {target.name}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
