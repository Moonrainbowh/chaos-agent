from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ._snapshot_artifacts import SnapshotArtifacts
from .edits import SnapshotEntry, WorkspaceSnapshot
from .errors import FileTooLargeError, WorkspaceError
from .paths import PathInput, WorkspacePathGuard


DEFAULT_MAX_TOTAL_BYTES = 10_000_000
DEFAULT_MAX_MANIFEST_BYTES = 1_000_000
_IDENTIFIER = re.compile(r"[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_HANDLE_KEYS = frozenset({"identifier", "digest", "paths", "total_bytes"})
_MANIFEST_KEYS = frozenset(
    {"version", "identifier", "workspace_fingerprint", "entries", "total_bytes"}
)
_ENTRY_KEYS = frozenset({"path", "existed", "digest", "size"})


@dataclass(frozen=True)
class SnapshotHandle:
    identifier: str
    digest: str
    paths: tuple[str, ...]
    total_bytes: int

    def __post_init__(self) -> None:
        if type(self.identifier) is not str or not _IDENTIFIER.fullmatch(self.identifier):
            raise ValueError("snapshot identifier must be 32 lowercase hex characters")
        if type(self.digest) is not str or not _DIGEST.fullmatch(self.digest):
            raise ValueError("snapshot digest must be 64 lowercase hex characters")
        if type(self.paths) is not tuple or any(type(path) is not str for path in self.paths):
            raise TypeError("snapshot paths must be a tuple of strings")
        if any(not _is_relative_path(path) for path in self.paths):
            raise ValueError("snapshot paths must be canonical relative paths")
        if len(set(self.paths)) != len(self.paths):
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


@dataclass(frozen=True)
class _StoredEntry:
    path: str
    existed: bool
    digest: str | None
    size: int

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "existed": self.existed,
            "digest": self.digest,
            "size": self.size,
        }


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
        self._workspace_fingerprint = _workspace_fingerprint(guard.root)

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
        manifest = _canonical_json(payload)
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
        manifest = self._artifacts.read_manifest(handle.identifier, self.max_manifest_bytes)
        if _sha256(manifest) != handle.digest:
            raise WorkspaceError("snapshot manifest digest does not match its handle")
        payload = _decode_manifest(manifest)
        entries = self._validate_manifest(payload, handle)
        loaded: list[SnapshotEntry] = []
        for entry in entries:
            content = self._load_blob(entry) if entry.existed else None
            loaded.append(SnapshotEntry(entry.path, content, entry.existed))
        return WorkspaceSnapshot(tuple(loaded))

    def _prepare_snapshot(
        self, snapshot: WorkspaceSnapshot
    ) -> tuple[tuple[_StoredEntry, ...], dict[str, bytes], int]:
        entries: list[_StoredEntry] = []
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
            entry, content = _stored_entry(path, source)
            total += entry.size
            if total > self.max_total_bytes:
                raise FileTooLargeError("snapshot exceeds its total byte limit")
            entries.append(entry)
            if content is not None:
                blobs[entry.digest or ""] = content
        return tuple(entries), blobs, total

    def _validate_manifest(
        self, payload: object, handle: SnapshotHandle
    ) -> tuple[_StoredEntry, ...]:
        if type(payload) is not dict or frozenset(payload) != _MANIFEST_KEYS:
            raise WorkspaceError("snapshot manifest fields are invalid")
        if type(payload["version"]) is not int or payload["version"] != 1:
            raise WorkspaceError("snapshot manifest version is invalid")
        if payload["identifier"] != handle.identifier:
            raise WorkspaceError("snapshot manifest identifier does not match")
        if payload["workspace_fingerprint"] != self._workspace_fingerprint:
            raise WorkspaceError("snapshot belongs to a different workspace")
        raw_entries = payload["entries"]
        if type(raw_entries) is not list:
            raise WorkspaceError("snapshot manifest entries must be a list")
        entries = tuple(self._manifest_entry(value) for value in raw_entries)
        self._check_manifest_totals(payload["total_bytes"], entries, handle)
        return entries

    def _manifest_entry(self, value: object) -> _StoredEntry:
        if type(value) is not dict or frozenset(value) != _ENTRY_KEYS:
            raise WorkspaceError("snapshot manifest entry fields are invalid")
        path = self._workspace_path(value["path"])
        existed, digest, size = value["existed"], value["digest"], value["size"]
        if type(existed) is not bool or type(size) is not int or size < 0:
            raise WorkspaceError("snapshot manifest entry types are invalid")
        if existed and (type(digest) is not str or not _DIGEST.fullmatch(digest)):
            raise WorkspaceError("snapshot blob digest is invalid")
        if not existed and (digest is not None or size != 0):
            raise WorkspaceError("missing snapshot entries cannot declare bytes")
        return _StoredEntry(path, existed, digest, size)

    def _check_manifest_totals(
        self, declared: object, entries: tuple[_StoredEntry, ...], handle: SnapshotHandle
    ) -> None:
        if type(declared) is not int or declared < 0:
            raise WorkspaceError("snapshot manifest total is invalid")
        paths = tuple(entry.path for entry in entries)
        if len({os.path.normcase(path) for path in paths}) != len(paths):
            raise WorkspaceError("snapshot manifest contains duplicate paths")
        total = sum(entry.size for entry in entries)
        if declared != total or declared != handle.total_bytes or paths != handle.paths:
            raise WorkspaceError("snapshot manifest does not match its handle")
        if total > self.max_total_bytes:
            raise FileTooLargeError("snapshot exceeds its total byte limit")

    def _workspace_path(self, value: object) -> str:
        if type(value) is not str or not _is_relative_path(value):
            raise WorkspaceError("snapshot path must be canonical and relative")
        target = self.guard.resolve(value)
        relative = self.guard.relative(target).as_posix()
        if relative != value:
            raise WorkspaceError("snapshot path is not canonical")
        return relative

    def _load_blob(self, entry: _StoredEntry) -> bytes:
        assert entry.digest is not None
        content = self._artifacts.read_blob(entry.digest, entry.size)
        if len(content) != entry.size or _sha256(content) != entry.digest:
            raise WorkspaceError(f"snapshot blob is corrupt: {entry.path}")
        return content


def _stored_entry(path: str, source: SnapshotEntry) -> tuple[_StoredEntry, bytes | None]:
    if type(source.existed) is not bool:
        raise TypeError("snapshot entry existence must be boolean")
    if not source.existed:
        if source.content is not None:
            raise ValueError("missing snapshot entries cannot contain bytes")
        return _StoredEntry(path, False, None, 0), None
    if type(source.content) is not bytes:
        raise TypeError("existing snapshot entries must contain bytes")
    digest = _sha256(source.content)
    return _StoredEntry(path, True, digest, len(source.content)), source.content


def _decode_manifest(raw: bytes) -> object:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkspaceError("snapshot manifest is not valid UTF-8 JSON") from error
    if _canonical_json(payload) != raw:
        raise WorkspaceError("snapshot manifest is not canonical JSON")
    return payload


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _limit(name: str, value: int, *, allow_zero: bool) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{name} is outside its supported range")
    return value


def _is_relative_path(value: str) -> bool:
    if not value or "\0" in value or "\\" in value or Path(value).is_absolute():
        return False
    parts = value.split("/")
    return all(part not in ("", ".", "..") for part in parts)


def _workspace_fingerprint(root: Path) -> str:
    identity = os.path.normcase(str(root.resolve(strict=True))).encode("utf-8")
    return _sha256(b"workspace-snapshot-v1\0" + identity)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
