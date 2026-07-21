from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ._secure_io import PathIdentity, identity_from_stat, is_regular
from ._snapshot_blob_io import BlobIntegrityFailure
from .errors import SearchTimeoutError, WorkspaceError, WorkspaceScanLimitError


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SHARD = re.compile(r"[0-9a-f]{2}\Z")


@dataclass(frozen=True)
class OrphanCandidate:
    digest: str
    path: Path
    identity: PathIdentity


def collect_orphans(
    blobs_root: Path,
    referenced: set[str],
    cutoff: float,
    *,
    max_entries: int,
    deadline: float,
    clock: Callable[[], float],
) -> tuple[OrphanCandidate, ...]:
    try:
        blobs_root.lstat()
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise WorkspaceError(f"cannot inspect blob store: {blobs_root}") from error
    _require_real_directory(blobs_root)
    candidates: list[OrphanCandidate] = []
    count = 0
    try:
        with os.scandir(blobs_root) as shards:
            for item in shards:
                shard = Path(item.path)
                count = _count_entry(count, max_entries, deadline, clock)
                if not _SHARD.fullmatch(shard.name):
                    continue
                _require_real_directory(shard)
                found, count = _scan_shard(
                    shard, referenced, cutoff, count, max_entries, deadline, clock
                )
                candidates.extend(found)
    except (BlobIntegrityFailure, SearchTimeoutError, WorkspaceScanLimitError):
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot scan blob store: {blobs_root}") from error
    _check_deadline(deadline, clock)
    return tuple(sorted(candidates, key=lambda candidate: candidate.digest))


def _scan_shard(
    shard: Path,
    referenced: set[str],
    cutoff: float,
    count: int,
    max_entries: int,
    deadline: float,
    clock: Callable[[], float],
) -> tuple[list[OrphanCandidate], int]:
    candidates: list[OrphanCandidate] = []
    try:
        with os.scandir(shard) as entries:
            for item in entries:
                path = Path(item.path)
                count = _count_entry(count, max_entries, deadline, clock)
                if not _DIGEST.fullmatch(path.name) or path.name[:2] != shard.name:
                    continue
                identity = _require_regular_blob(path)
                if path.name not in referenced and identity.modified_ns / 1e9 < cutoff:
                    candidates.append(OrphanCandidate(path.name, path, identity))
    except BlobIntegrityFailure:
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot scan blob shard: {shard}") from error
    return candidates, count


def delete_candidates(
    candidates: tuple[OrphanCandidate, ...],
    cutoff: float,
    deadline: float,
    clock: Callable[[], float],
) -> tuple[str, ...]:
    deleted: list[str] = []
    for candidate in candidates:
        _check_deadline(deadline, clock)
        try:
            current = _require_regular_blob(candidate.path)
        except BlobIntegrityFailure as error:
            try:
                candidate.path.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                raise error
            raise error
        if current != candidate.identity:
            raise WorkspaceError(f"blob changed during garbage collection: {candidate.path}")
        if current.modified_ns / 1e9 >= cutoff:
            continue
        try:
            candidate.path.unlink()
        except OSError as error:
            raise WorkspaceError(f"cannot delete orphan blob: {candidate.path}") from error
        deleted.append(candidate.digest)
    _check_deadline(deadline, clock)
    return tuple(deleted)


def _count_entry(
    count: int,
    max_entries: int,
    deadline: float,
    clock: Callable[[], float],
) -> int:
    _check_deadline(deadline, clock)
    count += 1
    if count > max_entries:
        raise WorkspaceScanLimitError(
            f"blob garbage collection exceeds {max_entries} entries"
        )
    return count


def _check_deadline(deadline: float, clock: Callable[[], float]) -> None:
    if clock() > deadline:
        raise SearchTimeoutError("blob garbage collection exceeded its deadline")


def _require_real_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise BlobIntegrityFailure(f"cannot inspect blob shard: {path}") from error
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise BlobIntegrityFailure(f"blob shard is not a real directory: {path}")
    if attributes & reparse:
        raise BlobIntegrityFailure(f"blob shard is a reparse point: {path}")


def _require_regular_blob(path: Path) -> PathIdentity:
    try:
        identity = identity_from_stat(path.lstat())
    except FileNotFoundError as error:
        raise BlobIntegrityFailure(f"missing blob during garbage collection: {path}") from error
    except OSError as error:
        raise BlobIntegrityFailure(f"cannot inspect blob during garbage collection: {path}") from error
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if not is_regular(identity) or stat.S_ISLNK(identity.mode):
        raise BlobIntegrityFailure(f"path is not a regular blob: {path}")
    if identity.attributes & reparse:
        raise BlobIntegrityFailure(f"path is not a regular blob: {path}")
    return identity
