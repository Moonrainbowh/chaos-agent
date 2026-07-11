from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .models import RepoEntry


@dataclass(frozen=True)
class FileSignature:
    """The file metadata used to decide whether a scan result remains valid."""

    size_bytes: int
    modified_ns: int


@dataclass(frozen=True)
class CachedScan:
    signature: FileSignature
    entry: RepoEntry
    source_facts: tuple[object, ...] = ()


class RepoMapCache:
    """A bounded LRU cache for successful individual-file repository scans."""

    def __init__(self, workspace_root: Path | None = None, *, max_entries: int = 5_000) -> None:
        if isinstance(max_entries, bool) or not isinstance(max_entries, int):
            raise TypeError("max_entries must be an integer")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.workspace_root = workspace_root.resolve() if workspace_root else None
        self.max_entries = max_entries
        self._entries: OrderedDict[Path, CachedScan] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def get_or_scan(self, path: Path, scan: Callable[[], RepoEntry]) -> RepoEntry:
        """Return a current entry, scanning only when file metadata changed."""
        entry, _ = self.get_or_scan_facts(path, lambda: (scan(), ()))
        return entry

    def get_or_scan_facts(
        self,
        path: Path,
        scan: Callable[[], tuple[RepoEntry, tuple[object, ...]]],
    ) -> tuple[RepoEntry, tuple[object, ...]]:
        """Return successful parse facts without treating derived data as cached."""
        resolved = self._resolve(path)
        try:
            signature = _signature(resolved)
        except OSError:
            return scan()
        cached = self._entries.get(resolved)
        if cached is not None and cached.signature == signature:
            self._entries.move_to_end(resolved)
            return cached.entry, cached.source_facts
        entry, source_facts = scan()
        self._entries[resolved] = CachedScan(signature, entry, source_facts)
        self._entries.move_to_end(resolved)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        return entry, source_facts

    def invalidate(self, paths: Iterable[str | Path]) -> None:
        for path in paths:
            self._entries.pop(self._resolve(path), None)

    def clear(self) -> None:
        self._entries.clear()

    def _resolve(self, path: str | Path) -> Path:
        candidate = Path(path)
        if self.workspace_root is not None and not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        return candidate.resolve(strict=False)


def _signature(path: Path) -> FileSignature:
    metadata = path.stat()
    return FileSignature(metadata.st_size, metadata.st_mtime_ns)
