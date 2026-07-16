from __future__ import annotations

import hashlib
import os
import re
import stat
import uuid
from pathlib import Path

from ._atomic_artifact_write import AtomicArtifactWriter
from ._guarded_read import read_guarded_file
from .errors import FileTooLargeError, WorkspaceError
from .paths import PathInput


_MANIFEST_NAME = re.compile(r"[0-9a-f]{32}\.json")
_BLOB_NAME = re.compile(r"[0-9a-f]{64}")


class SnapshotArtifacts:
    """Own the two fixed product-state artifact directories."""

    def __init__(self, product_state_root: PathInput, workspace_root: Path) -> None:
        self.root = _prepare_root(product_state_root, workspace_root)
        self.guard = _ArtifactPathGuard(self.root)
        self.writer = AtomicArtifactWriter(self.root)

    def new_identifier(self) -> str:
        for _ in range(8):
            identifier = uuid.uuid4().hex
            if not self._path(f"manifests/{identifier}.json").exists():
                return identifier
        raise WorkspaceError("cannot allocate a unique snapshot identifier")

    def read_manifest(self, identifier: str, limit: int) -> bytes:
        return self._read(f"manifests/{identifier}.json", limit)

    def write_manifest(self, identifier: str, content: bytes) -> None:
        self.writer.write(self._path(f"manifests/{identifier}.json"), content)

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
        self.writer.write(target, content)

    def _read(self, relative: str, limit: int) -> bytes:
        content = read_guarded_file(relative, self.guard, limit)
        if len(content) > limit:
            raise FileTooLargeError(f"snapshot artifact exceeds {limit} bytes")
        return content

    def _path(self, relative: str) -> Path:
        return self.guard.resolve(relative, for_write=True)


class _ArtifactPathGuard:
    """Contain access to the two fixed artifact namespaces without config policy."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve(self, path: PathInput, *, for_write: bool = False) -> Path:
        del for_write
        raw = os.fspath(path)
        if not isinstance(raw, str) or "\0" in raw:
            raise WorkspaceError("snapshot artifact path must be text")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            lexical = candidate.relative_to(self.root)
        except ValueError as error:
            raise WorkspaceError("snapshot artifact path escapes its fixed root") from error
        _validate_fixed_path(lexical)
        _reject_link_components(candidate)
        try:
            resolved = candidate.resolve(strict=False)
            relative = resolved.relative_to(self.root)
        except (OSError, RuntimeError, ValueError) as error:
            raise WorkspaceError("snapshot artifact path cannot be contained") from error
        if relative != lexical:
            raise WorkspaceError("snapshot artifact path changed during resolution")
        _validate_fixed_path(relative)
        return resolved


def _prepare_root(value: PathInput, workspace: Path) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str):
        raise TypeError("product-state root must be text")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError("product-state root must be absolute")
    if _contains_git_component(candidate):
        raise ValueError("product-state root cannot be inside .git")
    _reject_link_components(candidate)
    root = candidate.resolve(strict=False)
    if _contains_git_component(root):
        raise ValueError("product-state root cannot be inside .git")
    if _is_within(root, workspace):
        raise ValueError("product-state root must be outside the workspace")
    try:
        root.mkdir(parents=True, exist_ok=True)
        _reject_link_components(root)
        for name in ("manifests", "blobs"):
            directory = root / name
            directory.mkdir(exist_ok=True)
            _reject_link_components(directory)
            if not directory.is_dir():
                raise WorkspaceError("snapshot artifact directory is unsafe")
    except WorkspaceError:
        raise
    except OSError as error:
        raise WorkspaceError("cannot prepare snapshot artifact root") from error
    return root


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_fixed_path(relative: Path) -> None:
    parts = relative.parts
    valid_manifest = (
        len(parts) == 2
        and parts[0] == "manifests"
        and _MANIFEST_NAME.fullmatch(parts[1]) is not None
    )
    valid_blob = (
        len(parts) == 2
        and parts[0] == "blobs"
        and _BLOB_NAME.fullmatch(parts[1]) is not None
    )
    if not (valid_manifest or valid_blob):
        raise WorkspaceError("snapshot artifact path is not a fixed manifest or blob")


def _contains_git_component(path: Path) -> bool:
    return any(part.casefold() == ".git" for part in path.parts)


def _reject_link_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if _is_link_like(current):
            raise WorkspaceError("snapshot artifact path contains a link or reparse point")


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
