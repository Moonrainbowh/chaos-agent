from __future__ import annotations

import math
import os
import stat
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

from ._rewind_snapshot_store import SnapshotHandle, WorkspaceSnapshotStore
from ._snapshot_blob_io import (
    BlobIntegrityFailure,
    SNAPSHOT_BLOB_TEMP_NAME_UNITS,
    blob_path as _blob_path,
    publish_blob,
    read_blob_if_present,
)
from ._snapshot_gc import GcBudget, collect_orphans, delete_candidates
from ._snapshot_manifest import (
    BlobRef,
    MaterializedSnapshot,
    SnapshotIntegrityError,
    SnapshotManifest,
    SnapshotManifestEntry,
    materialized,
    prepare_manifest,
    require_digest,
    validate_manifest,
)
from .edits import SnapshotEntry, WorkspaceSnapshot
from .paths import PathInput
from .windows_paths import require_supported_windows_path


__all__ = (
    "ContentAddressedSnapshotStore",
    "SnapshotHandle",
    "WorkspaceSnapshotStore",
)

DEFAULT_MAX_SNAPSHOT_FILES = 10_000
DEFAULT_MAX_SNAPSHOT_BYTES = 100_000_000
DEFAULT_MAX_SNAPSHOT_FILE_BYTES = 10_000_000
DEFAULT_GC_MAX_ENTRIES = 10_000
DEFAULT_GC_DEADLINE_S = 30.0


class ContentAddressedSnapshotStore:
    """Persist immutable snapshot bytes without user snapshot metadata files."""

    def __init__(
        self,
        root: PathInput,
        *,
        read_fallback_roots: Iterable[PathInput] = (),
        max_files: int = DEFAULT_MAX_SNAPSHOT_FILES,
        max_total_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
        max_file_bytes: int = DEFAULT_MAX_SNAPSHOT_FILE_BYTES,
        gc_max_entries: int = DEFAULT_GC_MAX_ENTRIES,
        gc_deadline_s: float = DEFAULT_GC_DEADLINE_S,
    ) -> None:
        literal_root = Path(root).expanduser()
        _require_content_store_paths(literal_root, "snapshot store")
        self.root = literal_root.resolve(strict=False)
        _require_content_store_paths(self.root, "snapshot store")
        self.blobs_root = self.root / "blobs"
        self.read_fallback_roots = _fallback_roots(
            read_fallback_roots, self.root
        )
        self._read_blob_roots = (
            self.blobs_root,
            *(item / "blobs" for item in self.read_fallback_roots),
        )
        self.max_files = _positive_int("max_files", max_files)
        self.max_total_bytes = _nonnegative_int("max_total_bytes", max_total_bytes)
        self.max_file_bytes = _positive_int("max_file_bytes", max_file_bytes)
        self.gc_max_entries = _positive_int("gc_max_entries", gc_max_entries)
        self.gc_deadline_s = _positive_number("gc_deadline_s", gc_deadline_s)

    def blob_path(self, blob_sha256: str) -> Path:
        """Return the fixed shard path for a validated content digest."""
        return _blob_path(self.blobs_root, require_digest(blob_sha256))

    def put(
        self, snapshot: WorkspaceSnapshot, modes: Mapping[str, int]
    ) -> SnapshotManifest:
        """Validate a complete snapshot, then publish each missing blob."""
        manifest, content_by_digest = prepare_manifest(
            snapshot,
            modes,
            self.max_files,
            self.max_total_bytes,
            self.max_file_bytes,
        )
        for digest in sorted(content_by_digest):
            try:
                publish_blob(
                    self.blobs_root,
                    digest,
                    content_by_digest[digest],
                    replace=os.replace,
                    fsync=os.fsync,
                )
            except BlobIntegrityFailure as error:
                raise SnapshotIntegrityError(str(error)) from error
        return manifest

    def materialize(self, manifest: SnapshotManifest) -> MaterializedSnapshot:
        """Verify the whole manifest and every blob before returning any bytes."""
        entries = validate_manifest(
            manifest,
            self.max_files,
            self.max_total_bytes,
            self.max_file_bytes,
        )
        snapshot_entries: list[SnapshotEntry] = []
        modes: dict[str, int] = {}
        for entry in entries:
            if not entry.existed:
                snapshot_entries.append(SnapshotEntry(entry.relative_path, None, False))
                continue
            content = self._read_manifest_blob(entry)
            snapshot_entries.append(SnapshotEntry(entry.relative_path, content, True))
            assert entry.mode is not None
            modes[entry.relative_path] = entry.mode
        return materialized(tuple(snapshot_entries), modes)

    def delete_orphans(
        self,
        referenced: Iterable[str | BlobRef],
        older_than: float | datetime,
    ) -> tuple[str, ...]:
        """Delete old unreferenced blobs after a bounded, non-mutating scan."""
        cutoff = _timestamp(older_than)
        deadline = time.monotonic() + self.gc_deadline_s
        budget = GcBudget(self.gc_max_entries, deadline, time.monotonic)
        references = _reference_digests(referenced, budget)
        try:
            candidates = collect_orphans(
                self.blobs_root,
                references,
                cutoff,
                budget=budget,
            )
            return delete_candidates(self.blobs_root, candidates, cutoff, budget)
        except BlobIntegrityFailure as error:
            raise SnapshotIntegrityError(str(error)) from error

    def _read_manifest_blob(self, entry: SnapshotManifestEntry) -> bytes:
        assert entry.blob_sha256 is not None
        label = f"snapshot blob for {entry.relative_path}"
        for root in self._read_blob_roots:
            path = _blob_path(root, entry.blob_sha256)
            try:
                content = read_blob_if_present(
                    path, entry.blob_sha256, entry.size, label
                )
            except BlobIntegrityFailure as error:
                raise SnapshotIntegrityError(str(error)) from error
            if content is not None:
                return content
        raise SnapshotIntegrityError(
            f"missing {label}: {self.blob_path(entry.blob_sha256)}"
        )


def _reference_digests(
    values: Iterable[str | BlobRef], budget: GcBudget
) -> set[str]:
    if isinstance(values, (str, bytes)):
        raise TypeError("referenced blobs must be an iterable of digests")
    result: set[str] = set()
    for value in values:
        budget.consume("referenced blobs")
        digest = value.sha256 if isinstance(value, BlobRef) else value
        result.add(require_digest(digest))
    return result


def _fallback_roots(
    values: Iterable[PathInput], primary: Path
) -> tuple[Path, ...]:
    if isinstance(values, (str, bytes, os.PathLike)):
        raise TypeError("read_fallback_roots must be an iterable of paths")
    roots = tuple(
        root
        for value in values
        if (root := _admit_fallback_root(value)) is not None
    )
    return tuple(dict.fromkeys(root for root in roots if root != primary))


def _admit_fallback_root(value: PathInput) -> Path | None:
    literal = Path(os.path.abspath(Path(value).expanduser()))
    _require_content_store_paths(literal, "snapshot fallback")
    blobs = literal / "blobs"
    current = Path(blobs.anchor)
    for part in blobs.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise SnapshotIntegrityError(
                f"cannot inspect snapshot fallback path: {current}"
            ) from error
        attributes = getattr(metadata, "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or bool(attributes & reparse)
        ):
            raise SnapshotIntegrityError(
                f"snapshot fallback path is not a real directory: {current}"
            )
    resolved = literal.resolve(strict=True)
    _require_content_store_paths(resolved, "snapshot fallback")
    return resolved


def _require_content_store_paths(root: Path, operation: str) -> None:
    blobs = root / "blobs"
    shard = blobs / "00"
    candidates = (
        root,
        blobs,
        shard,
        shard / ("0" * 64),
        shard / ("t" * SNAPSHOT_BLOB_TEMP_NAME_UNITS),
    )
    for candidate in candidates:
        require_supported_windows_path(candidate, operation=operation)


def _timestamp(value: float | datetime) -> float:
    timestamp = value.timestamp() if isinstance(value, datetime) else value
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise TypeError("older_than must be a timestamp or datetime")
    if not math.isfinite(timestamp):
        raise ValueError("older_than must be finite")
    return float(timestamp)


def _positive_int(name: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _nonnegative_int(name: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value


def _positive_number(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return float(value)
