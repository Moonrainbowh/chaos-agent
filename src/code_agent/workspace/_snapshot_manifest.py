from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Mapping

from ._secure_io import canonical_path_key
from .edits import SnapshotEntry, WorkspaceSnapshot
from .errors import FileTooLargeError, WorkspaceError, WorkspaceScanLimitError


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class SnapshotIntegrityError(WorkspaceError):
    """Raised when a manifest or immutable blob fails validation."""


@dataclass(frozen=True)
class BlobRef:
    sha256: str
    size: int


@dataclass(frozen=True)
class SnapshotManifestEntry:
    relative_path: str
    existed: bool
    blob_sha256: str | None
    size: int
    mode: int | None


@dataclass(frozen=True)
class SnapshotManifest:
    entries: tuple[SnapshotManifestEntry, ...]
    inventory_digest: str
    total_bytes: int


@dataclass(frozen=True)
class MaterializedSnapshot:
    snapshot: WorkspaceSnapshot
    modes: Mapping[str, int]


def prepare_manifest(
    snapshot: WorkspaceSnapshot,
    modes: Mapping[str, int],
    max_files: int,
    max_total_bytes: int,
    max_file_bytes: int,
) -> tuple[SnapshotManifest, dict[str, bytes]]:
    if not isinstance(snapshot, WorkspaceSnapshot):
        raise TypeError("snapshot must be a WorkspaceSnapshot")
    if not isinstance(modes, Mapping):
        raise TypeError("modes must be a mapping")
    check_file_count(len(snapshot.entries), max_files)
    ordered = _ordered_snapshot_entries(snapshot.entries)
    mode_by_path = _validate_modes(ordered, modes)
    entries, content_by_digest, total = _encode_entries(
        ordered, mode_by_path, max_total_bytes, max_file_bytes
    )
    return SnapshotManifest(entries, manifest_digest(entries), total), content_by_digest


def validate_manifest(
    manifest: SnapshotManifest,
    max_files: int,
    max_total_bytes: int,
    max_file_bytes: int,
) -> tuple[SnapshotManifestEntry, ...]:
    if not isinstance(manifest, SnapshotManifest):
        raise TypeError("manifest must be a SnapshotManifest")
    if not isinstance(manifest.entries, tuple):
        raise SnapshotIntegrityError("manifest entries must be a tuple")
    check_file_count(len(manifest.entries), max_files)
    entries = _validate_manifest_entries(manifest.entries, max_file_bytes)
    total = sum(entry.size for entry in entries if entry.existed)
    if total > max_total_bytes:
        raise FileTooLargeError(f"snapshot exceeds {max_total_bytes} total bytes")
    if (
        not isinstance(manifest.total_bytes, int)
        or isinstance(manifest.total_bytes, bool)
        or manifest.total_bytes != total
    ):
        raise SnapshotIntegrityError("manifest total bytes do not match entries")
    if manifest.inventory_digest != manifest_digest(entries):
        raise SnapshotIntegrityError("manifest digest does not match entries")
    return entries


def materialized(
    entries: tuple[SnapshotEntry, ...], modes: dict[str, int]
) -> MaterializedSnapshot:
    return MaterializedSnapshot(WorkspaceSnapshot(entries), MappingProxyType(modes))


def require_digest(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("blob SHA-256 must be text")
    if not _DIGEST.fullmatch(value):
        raise ValueError("blob SHA-256 must be 64 lowercase hexadecimal characters")
    return value


def _encode_entries(
    entries: tuple[SnapshotEntry, ...],
    modes: dict[str, int],
    max_total_bytes: int,
    max_file_bytes: int,
) -> tuple[tuple[SnapshotManifestEntry, ...], dict[str, bytes], int]:
    manifest: list[SnapshotManifestEntry] = []
    content_by_digest: dict[str, bytes] = {}
    total = 0
    for entry in entries:
        if not entry.existed:
            manifest.append(SnapshotManifestEntry(entry.relative_path, False, None, 0, None))
            continue
        assert entry.content is not None
        _check_file_size(entry.relative_path, len(entry.content), max_file_bytes)
        total = _add_total(total, len(entry.content), max_total_bytes)
        digest = hashlib.sha256(entry.content).hexdigest()
        content_by_digest.setdefault(digest, entry.content)
        manifest.append(
            SnapshotManifestEntry(
                entry.relative_path, True, digest, len(entry.content), modes[entry.relative_path]
            )
        )
    return tuple(manifest), content_by_digest, total


def _ordered_snapshot_entries(
    entries: tuple[SnapshotEntry, ...]
) -> tuple[SnapshotEntry, ...]:
    seen: set[str] = set()
    checked: list[SnapshotEntry] = []
    for entry in entries:
        if not isinstance(entry, SnapshotEntry):
            raise TypeError("snapshot entries must be SnapshotEntry values")
        _require_relative_path(entry.relative_path)
        key = canonical_path_key(entry.relative_path)
        if key in seen:
            raise ValueError(f"duplicate snapshot path: {entry.relative_path}")
        seen.add(key)
        checked.append(entry)
    return tuple(sorted(checked, key=lambda entry: canonical_path_key(entry.relative_path)))


def _validate_modes(
    entries: tuple[SnapshotEntry, ...], modes: Mapping[str, int]
) -> dict[str, int]:
    expected = {entry.relative_path for entry in entries if entry.existed}
    if set(modes) != expected:
        raise ValueError("modes must exactly match existing snapshot paths")
    checked: dict[str, int] = {}
    for path, mode in modes.items():
        _require_relative_path(path)
        if not isinstance(mode, int) or isinstance(mode, bool):
            raise TypeError("snapshot modes must be integers")
        if mode < 0 or mode > 0o7777:
            raise ValueError("snapshot modes must contain only POSIX permission bits")
        checked[path] = mode
    return checked


def _validate_manifest_entries(
    entries: tuple[SnapshotManifestEntry, ...], max_file_bytes: int
) -> tuple[SnapshotManifestEntry, ...]:
    seen: set[str] = set()
    previous: str | None = None
    for entry in entries:
        if not isinstance(entry, SnapshotManifestEntry):
            raise SnapshotIntegrityError("invalid manifest entry type")
        _require_relative_path(entry.relative_path, integrity=True)
        key = canonical_path_key(entry.relative_path)
        if key in seen:
            raise SnapshotIntegrityError(f"duplicate manifest path: {entry.relative_path}")
        if previous is not None and key < previous:
            raise SnapshotIntegrityError("manifest entries are not sorted")
        seen.add(key)
        previous = key
        _validate_manifest_entry(entry, max_file_bytes)
    return entries


def _validate_manifest_entry(entry: SnapshotManifestEntry, max_file_bytes: int) -> None:
    if not isinstance(entry.existed, bool):
        raise SnapshotIntegrityError("manifest existed flag must be boolean")
    if not isinstance(entry.size, int) or isinstance(entry.size, bool) or entry.size < 0:
        raise SnapshotIntegrityError("manifest entry size is invalid")
    if not entry.existed:
        if entry.blob_sha256 is not None or entry.size != 0 or entry.mode is not None:
            raise SnapshotIntegrityError("tombstone metadata is inconsistent")
        return
    try:
        require_digest(entry.blob_sha256)
    except (TypeError, ValueError) as error:
        raise SnapshotIntegrityError("existing entry has an invalid blob digest") from error
    if entry.size > max_file_bytes:
        raise FileTooLargeError(f"file exceeds {max_file_bytes} bytes: {entry.relative_path}")
    if not isinstance(entry.mode, int) or isinstance(entry.mode, bool):
        raise SnapshotIntegrityError("existing entry has an invalid mode")
    if entry.mode < 0 or entry.mode > 0o7777:
        raise SnapshotIntegrityError("existing entry mode exceeds POSIX permission bits")


def manifest_digest(entries: tuple[SnapshotManifestEntry, ...]) -> str:
    values = [
        [entry.relative_path, entry.size, entry.blob_sha256, entry.mode]
        for entry in entries
        if entry.existed
    ]
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_relative_path(path: object, *, integrity: bool = False) -> str:
    error_type = SnapshotIntegrityError if integrity else ValueError
    if not isinstance(path, str):
        raise TypeError("snapshot path must be text")
    candidate = PurePosixPath(path)
    windows = PureWindowsPath(path)
    if (
        not path
        or "\0" in path
        or "\\" in path
        or candidate.is_absolute()
        or bool(windows.drive)
    ):
        raise error_type(f"snapshot path is not canonical: {path!r}")
    if candidate.as_posix() != path or any(part in (".", "..") for part in candidate.parts):
        raise error_type(f"snapshot path is not canonical: {path!r}")
    return path


def check_file_count(count: int, maximum: int) -> None:
    if count > maximum:
        raise WorkspaceScanLimitError(f"snapshot exceeds {maximum} files")


def _check_file_size(path: str, size: int, maximum: int) -> None:
    if size > maximum:
        raise FileTooLargeError(f"file exceeds {maximum} bytes: {path}")


def _add_total(total: int, size: int, maximum: int) -> int:
    total += size
    if total > maximum:
        raise FileTooLargeError(f"snapshot exceeds {maximum} total bytes")
    return total
