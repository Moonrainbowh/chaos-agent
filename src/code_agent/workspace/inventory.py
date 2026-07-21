from __future__ import annotations

import hashlib
import json
import math
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import (
    FileTooLargeError,
    SearchTimeoutError,
    SensitivePathError,
    WorkspaceError,
    WorkspaceScanLimitError,
)
from .paths import PathInput, WorkspacePathGuard


DEFAULT_MAX_INVENTORY_FILES = 10_000
DEFAULT_MAX_INVENTORY_BYTES = 100_000_000
DEFAULT_MAX_INVENTORY_FILE_BYTES = 10_000_000
DEFAULT_INVENTORY_DEADLINE_S = 30.0


class SnapshotPaths(Protocol):
    def snapshot_paths(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class InventoryEntry:
    relative_path: str
    size: int
    sha256: str
    mode: int


@dataclass(frozen=True)
class WorkspaceInventory:
    entries: tuple[InventoryEntry, ...]
    digest: str

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(entry.relative_path for entry in self.entries)

    @classmethod
    def capture(
        cls,
        root: PathInput,
        guard: WorkspacePathGuard,
        git: SnapshotPaths,
        *,
        max_files: int = DEFAULT_MAX_INVENTORY_FILES,
        max_total_bytes: int = DEFAULT_MAX_INVENTORY_BYTES,
        max_file_bytes: int = DEFAULT_MAX_INVENTORY_FILE_BYTES,
        deadline_s: float = DEFAULT_INVENTORY_DEADLINE_S,
    ) -> WorkspaceInventory:
        """Capture a bounded manifest of eligible existing Git paths."""
        limits = _validate_limits(
            max_files, max_total_bytes, max_file_bytes, deadline_s
        )
        _require_matching_roots(root, guard, git)
        deadline = time.monotonic() + limits.deadline_s
        candidates = git.snapshot_paths()
        _check_deadline(deadline)
        if len(candidates) > limits.max_files:
            raise WorkspaceScanLimitError(
                f"inventory exceeds {limits.max_files} files"
            )
        entries = _capture_entries(candidates, guard, limits, deadline)
        ordered = tuple(sorted(entries, key=lambda entry: entry.relative_path))
        return cls(ordered, _manifest_digest(ordered))


@dataclass(frozen=True)
class _InventoryLimits:
    max_files: int
    max_total_bytes: int
    max_file_bytes: int
    deadline_s: float


def workspace_fingerprint(inventory: WorkspaceInventory) -> str:
    """Return the canonical digest used to detect a stale workspace preview."""
    if not isinstance(inventory, WorkspaceInventory):
        raise TypeError("inventory must be a WorkspaceInventory")
    return inventory.digest


def _validate_limits(
    max_files: int,
    max_total_bytes: int,
    max_file_bytes: int,
    deadline_s: float,
) -> _InventoryLimits:
    for name, value in (
        ("max_files", max_files),
        ("max_total_bytes", max_total_bytes),
        ("max_file_bytes", max_file_bytes),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"{name} must be an integer")
        if value < 0:
            raise ValueError(f"{name} cannot be negative")
    if max_files == 0 or max_file_bytes == 0:
        raise ValueError("file limits must be positive")
    if isinstance(deadline_s, bool) or not isinstance(deadline_s, (int, float)):
        raise TypeError("deadline_s must be a number")
    if not math.isfinite(deadline_s) or deadline_s <= 0:
        raise ValueError("deadline_s must be positive and finite")
    return _InventoryLimits(
        max_files, max_total_bytes, max_file_bytes, float(deadline_s)
    )


def _require_matching_roots(
    root: PathInput, guard: WorkspacePathGuard, git: SnapshotPaths
) -> None:
    requested = Path(root).expanduser().resolve(strict=True)
    if requested != guard.root:
        raise ValueError("inventory root and path guard root must match")
    git_root = getattr(git, "root", requested)
    if Path(git_root).resolve(strict=True) != requested:
        raise ValueError("inventory root and Git root must match")


def _capture_entries(
    candidates: tuple[str, ...],
    guard: WorkspacePathGuard,
    limits: _InventoryLimits,
    deadline: float,
) -> list[InventoryEntry]:
    entries: list[InventoryEntry] = []
    seen: set[str] = set()
    total = 0
    for candidate in candidates:
        _check_deadline(deadline)
        try:
            target = guard.resolve(candidate)
        except SensitivePathError:
            continue
        relative = guard.relative(target).as_posix()
        if relative in seen:
            raise ValueError(f"duplicate inventory path: {relative}")
        seen.add(relative)
        entry = _read_entry(target, relative, limits.max_file_bytes)
        if entry is None:
            continue
        total += entry.size
        if total > limits.max_total_bytes:
            raise FileTooLargeError(
                f"inventory exceeds {limits.max_total_bytes} total bytes"
            )
        entries.append(entry)
    _check_deadline(deadline)
    return entries


def _read_entry(
    target: Path, relative: str, max_file_bytes: int
) -> InventoryEntry | None:
    if not target.exists():
        return None
    try:
        metadata = target.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise WorkspaceError(f"not a regular file: {target}")
        if metadata.st_size > max_file_bytes:
            raise FileTooLargeError(
                f"file exceeds {max_file_bytes} bytes: {target}"
            )
        with target.open("rb") as stream:
            content = stream.read(max_file_bytes + 1)
    except (FileTooLargeError, WorkspaceError):
        raise
    except OSError as error:
        raise WorkspaceError(f"cannot read inventory file: {target}") from error
    if len(content) > max_file_bytes:
        raise FileTooLargeError(f"file exceeds {max_file_bytes} bytes: {target}")
    return InventoryEntry(
        relative,
        len(content),
        hashlib.sha256(content).hexdigest(),
        stat.S_IMODE(metadata.st_mode),
    )


def _manifest_digest(entries: tuple[InventoryEntry, ...]) -> str:
    manifest = [
        [entry.relative_path, entry.size, entry.sha256, entry.mode]
        for entry in entries
    ]
    encoded = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise SearchTimeoutError("workspace inventory exceeded its deadline")
