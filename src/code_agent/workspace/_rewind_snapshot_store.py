from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from ._rewind_snapshot_manifest import (
    StoredEntry,
    canonical_json,
    decode_manifest,
    is_digest,
    is_identifier,
    is_relative_path,
    stored_entry,
    validate_manifest,
)
from ._snapshot_artifacts import SnapshotArtifacts
from .edits import SnapshotEntry, WorkspaceSnapshot
from .errors import (
    FileTooLargeError,
    SnapshotIntegrityError,
    SnapshotMissingError,
    WorkspaceError,
)
from .paths import PathInput, WorkspacePathGuard


DEFAULT_MAX_TOTAL_BYTES = 10_000_000
DEFAULT_MAX_MANIFEST_BYTES = 1_000_000
_HANDLE_KEYS = frozenset({"identifier", "digest", "paths", "total_bytes"})


@dataclass(frozen=True)
class SnapshotHandle:
    identifier: str
    digest: str
    paths: tuple[str, ...]
    total_bytes: int

    def __post_init__(self) -> None:
        if not is_identifier(self.identifier):
            raise ValueError("snapshot identifier must be 32 lowercase hex characters")
        if not is_digest(self.digest):
            raise ValueError("snapshot digest must be 64 lowercase hex characters")
        if type(self.paths) is not tuple or any(type(path) is not str for path in self.paths):
            raise TypeError("snapshot paths must be a tuple of strings")
        if any(not is_relative_path(path) for path in self.paths):
            raise ValueError("snapshot paths must be canonical relative paths")
        if len({os.path.normcase(path) for path in self.paths}) != len(self.paths):
            raise ValueError("snapshot paths must be unique")
        if type(self.total_bytes) is not int:
            raise TypeError("snapshot total_bytes must be an integer")
        if self.total_bytes < 0:
            raise ValueError("snapshot total_bytes cannot be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "digest": self.digest,
            "paths": list(self.paths),
            "total_bytes": self.total_bytes,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SnapshotHandle":
        if type(value) is not dict:
            raise TypeError("snapshot handle must be an object")
        if frozenset(value) != _HANDLE_KEYS:
            raise ValueError("snapshot handle fields are invalid")
        paths = value["paths"]
        if type(paths) is not list:
            raise TypeError("snapshot handle paths must be a list")
        return cls(
            value["identifier"],  # type: ignore[arg-type]
            value["digest"],  # type: ignore[arg-type]
            tuple(paths),
            value["total_bytes"],  # type: ignore[arg-type]
        )


class WorkspaceSnapshotStore:
    """Persist guarded byte snapshots under an injected product-state root."""

    def __init__(
        self,
        guard: WorkspacePathGuard,
        product_state_root: PathInput,
        *,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        max_manifest_bytes: int = DEFAULT_MAX_MANIFEST_BYTES,
    ) -> None:
        if not isinstance(guard, WorkspacePathGuard):
            raise TypeError("guard must be a WorkspacePathGuard")
        self.max_total_bytes = _limit("max_total_bytes", max_total_bytes, allow_zero=True)
        self.max_manifest_bytes = _limit(
            "max_manifest_bytes", max_manifest_bytes, allow_zero=False
        )
        self.guard = guard
        self._artifacts = SnapshotArtifacts(product_state_root, guard.root)
        self.root = self._artifacts.root
        self._workspace_fingerprint = _workspace_fingerprint(guard)

    @property
    def workspace_fingerprint(self) -> str:
        return self._workspace_fingerprint

    def save(self, snapshot: WorkspaceSnapshot) -> SnapshotHandle:
        if type(snapshot) is not WorkspaceSnapshot:
            raise TypeError("snapshot must be a WorkspaceSnapshot")
        entries, blobs, total = self._prepare_snapshot(snapshot)
        identifier = self._artifacts.new_identifier()
        payload = {
            "version": 1,
            "identifier": identifier,
            "workspace_fingerprint": self._workspace_fingerprint,
            "entries": [entry.to_dict() for entry in entries],
            "total_bytes": total,
        }
        manifest = canonical_json(payload)
        if len(manifest) > self.max_manifest_bytes:
            raise FileTooLargeError("snapshot manifest exceeds its byte limit")
        for digest, content in blobs.items():
            self._artifacts.ensure_blob(digest, content)
        self._artifacts.write_manifest(identifier, manifest)
        return SnapshotHandle(
            identifier,
            _sha256(manifest),
            tuple(entry.path for entry in entries),
            total,
        )

    def load(self, handle: SnapshotHandle) -> WorkspaceSnapshot:
        if type(handle) is not SnapshotHandle:
            raise TypeError("handle must be a SnapshotHandle")
        try:
            return self._load_referenced(handle)
        except (SnapshotMissingError, SnapshotIntegrityError):
            raise
        except WorkspaceError as error:
            raise SnapshotIntegrityError(
                "referenced snapshot failed integrity validation"
            ) from error

    def _load_referenced(self, handle: SnapshotHandle) -> WorkspaceSnapshot:
        manifest = self._artifacts.read_manifest(
            handle.identifier, self.max_manifest_bytes
        )
        if _sha256(manifest) != handle.digest:
            raise WorkspaceError("snapshot manifest digest does not match its handle")
        payload = decode_manifest(manifest)
        entries = validate_manifest(
            payload,
            identifier=handle.identifier,
            workspace_fingerprint=self._workspace_fingerprint,
            handle_paths=handle.paths,
            handle_total_bytes=handle.total_bytes,
            max_total_bytes=self.max_total_bytes,
            canonicalize_path=self._workspace_path,
        )
        loaded: list[SnapshotEntry] = []
        for entry in entries:
            content = self._load_blob(entry) if entry.existed else None
            loaded.append(SnapshotEntry(entry.path, content, entry.existed))
        return WorkspaceSnapshot(tuple(loaded))

    def _prepare_snapshot(
        self, snapshot: WorkspaceSnapshot
    ) -> tuple[tuple[StoredEntry, ...], dict[str, bytes], int]:
        entries: list[StoredEntry] = []
        blobs: dict[str, bytes] = {}
        seen: set[str] = set()
        total = 0
        for source in snapshot.entries:
            if type(source) is not SnapshotEntry:
                raise TypeError("snapshot entries must be SnapshotEntry values")
            path = self._workspace_path(source.relative_path)
            key = os.path.normcase(path)
            if key in seen:
                raise ValueError(f"duplicate snapshot path: {path}")
            seen.add(key)
            entry, content = stored_entry(path, source, _sha256)
            total += entry.size
            if total > self.max_total_bytes:
                raise FileTooLargeError("snapshot exceeds its total byte limit")
            entries.append(entry)
            if content is not None:
                blobs[entry.digest or ""] = content
        return tuple(entries), blobs, total

    def _workspace_path(self, value: object) -> str:
        if type(value) is not str or not is_relative_path(value):
            raise WorkspaceError("snapshot path must be canonical and relative")
        resolved = self.guard.resolve(value)
        try:
            canonical = resolved.relative_to(self.guard.root).as_posix()
        except ValueError as error:
            raise WorkspaceError("snapshot path escaped its workspace") from error
        case_only = os.name == "nt" and canonical.casefold() == value.casefold()
        if canonical != value and not case_only:
            raise WorkspaceError("snapshot path must use its canonical spelling")
        return value

    def _load_blob(self, entry: StoredEntry) -> bytes:
        assert entry.digest is not None
        content = self._artifacts.read_blob(entry.digest, entry.size)
        if len(content) != entry.size or _sha256(content) != entry.digest:
            raise WorkspaceError(f"snapshot blob is corrupt: {entry.path}")
        return content


def _limit(name: str, value: int, *, allow_zero: bool) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{name} is outside its supported range")
    return value


def _workspace_fingerprint(guard: WorkspacePathGuard) -> str:
    path = os.path.normcase(str(guard.root)).encode("utf-8")
    native = ":".join(str(value) for value in guard.root_identity).encode("ascii")
    identity = path + b"\0" + native
    return _sha256(b"workspace-snapshot-v1\0" + identity)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
