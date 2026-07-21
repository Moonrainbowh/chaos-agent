from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import _posix_io
from ._secure_io import PathIdentity
from ._snapshot_store_dirs import (
    BlobIntegrityFailure,
    StoreDirectory,
    close_directory,
    inspect_regular,
    open_shard,
    open_store_root,
    scan_target,
    verify_chain,
    verify_directory,
)
from .errors import SearchTimeoutError, WorkspaceError, WorkspaceScanLimitError


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SHARD = re.compile(r"[0-9a-f]{2}\Z")


@dataclass(frozen=True)
class OrphanCandidate:
    digest: str
    shard_name: str
    identity: PathIdentity
    shard_identity: PathIdentity
    root_identity: PathIdentity


@dataclass
class GcBudget:
    max_entries: int
    deadline: float
    clock: Callable[[], float]
    entries: int = 0

    def consume(self, label: str = "blob garbage collection") -> None:
        self.check()
        self.entries += 1
        if self.entries > self.max_entries:
            raise WorkspaceScanLimitError(
                f"{label} exceeds {self.max_entries} entries"
            )

    def check(self) -> None:
        if self.clock() > self.deadline:
            raise SearchTimeoutError(
                "blob garbage collection exceeded its deadline"
            )


def collect_orphans(
    blobs_root: Path,
    referenced: set[str],
    cutoff: float,
    *,
    budget: GcBudget,
) -> tuple[OrphanCandidate, ...]:
    root = open_store_root(blobs_root, create=False)
    if root is None:
        return ()
    candidates: list[OrphanCandidate] = []
    try:
        verify_directory(root, "blob root")
        with os.scandir(scan_target(root)) as shards:
            for item in shards:
                budget.consume()
                if not _SHARD.fullmatch(item.name):
                    continue
                shard = open_shard(root, item.name, create=False)
                if shard is None:
                    raise BlobIntegrityFailure(
                        f"blob shard disappeared: {root.path / item.name}"
                    )
                try:
                    candidates.extend(
                        _scan_shard(root, shard, referenced, cutoff, budget)
                    )
                finally:
                    close_directory(shard)
        verify_directory(root, "blob root")
        budget.check()
    except (BlobIntegrityFailure, SearchTimeoutError, WorkspaceScanLimitError):
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot scan blob store: {blobs_root}") from error
    finally:
        close_directory(root)
    return tuple(sorted(candidates, key=lambda candidate: candidate.digest))


def _scan_shard(
    root: StoreDirectory,
    shard: StoreDirectory,
    referenced: set[str],
    cutoff: float,
    budget: GcBudget,
) -> list[OrphanCandidate]:
    candidates: list[OrphanCandidate] = []
    verify_chain(root, shard)
    try:
        with os.scandir(scan_target(shard)) as entries:
            for item in entries:
                budget.consume()
                if not _DIGEST.fullmatch(item.name):
                    continue
                if item.name[:2] != shard.path.name:
                    raise BlobIntegrityFailure(
                        f"blob digest is stored in the wrong shard: {shard.path / item.name}"
                    )
                identity = inspect_regular(shard, item.name, "garbage collection blob")
                assert identity is not None
                if item.name not in referenced and identity.modified_ns / 1e9 < cutoff:
                    candidates.append(
                        OrphanCandidate(
                            item.name,
                            shard.path.name,
                            identity,
                            shard.identity,
                            root.identity,
                        )
                    )
    except BlobIntegrityFailure:
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot scan blob shard: {shard.path}") from error
    verify_chain(root, shard)
    return candidates


def delete_candidates(
    blobs_root: Path,
    candidates: tuple[OrphanCandidate, ...],
    cutoff: float,
    budget: GcBudget,
) -> tuple[str, ...]:
    deleted: list[str] = []
    for candidate in candidates:
        budget.check()
        if _delete_candidate(blobs_root, candidate, cutoff):
            deleted.append(candidate.digest)
    budget.check()
    return tuple(deleted)


def _delete_candidate(
    blobs_root: Path, candidate: OrphanCandidate, cutoff: float
) -> bool:
    root = open_store_root(blobs_root, create=False)
    if root is None or root.identity != candidate.root_identity:
        close_directory(root)
        raise BlobIntegrityFailure("blob root changed during garbage collection")
    shard: StoreDirectory | None = None
    try:
        shard = open_shard(root, candidate.shard_name, create=False)
        if shard is None or shard.identity != candidate.shard_identity:
            raise BlobIntegrityFailure("blob shard changed during garbage collection")
        current = inspect_regular(
            shard, candidate.digest, "garbage collection blob", missing_ok=True
        )
        if current is None:
            return False
        if current != candidate.identity:
            raise WorkspaceError(
                f"blob changed during garbage collection: {shard.path / candidate.digest}"
            )
        if current.modified_ns / 1e9 >= cutoff:
            return False
        _unlink_candidate(root, shard, candidate.digest)
        return True
    finally:
        close_directory(shard)
        close_directory(root)


def _unlink_candidate(
    root: StoreDirectory, shard: StoreDirectory, digest: str
) -> None:
    verify_chain(root, shard)
    try:
        if shard.descriptor is not None:
            _posix_io.unlink(shard.descriptor, digest)
        else:
            (shard.path / digest).unlink()
    except OSError as error:
        raise WorkspaceError(
            f"cannot delete orphan blob: {shard.path / digest}"
        ) from error
    verify_chain(root, shard)
    if inspect_regular(
        shard, digest, "garbage collection blob", missing_ok=True
    ) is not None:
        raise WorkspaceError(f"cannot delete orphan blob: {shard.path / digest}")
