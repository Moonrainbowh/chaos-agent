from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock

from code_agent.workspace.errors import WorkspaceError
from code_agent.workspace.files import WorkspaceFiles

from .cache import FileSignature
from .errors import RepoMapError
from .models import RepoEntry
from .repo_scan import ImportRef, RepoFileFacts, RepoFileScanner


@dataclass(frozen=True)
class RepoIndexSnapshot:
    """One immutable, internally consistent repository fact generation."""

    generation: int
    entries: tuple[RepoEntry, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.generation, bool) or not isinstance(
            self.generation, int
        ):
            raise TypeError("generation must be an integer")
        if self.generation < 0:
            raise ValueError("generation must not be negative")
        entries = tuple(self.entries)
        if not all(isinstance(item, RepoEntry) for item in entries):
            raise TypeError("entries must contain RepoEntry values")
        object.__setattr__(self, "entries", entries)


class RepoIndexService:
    """Own one lazily initialized, incrementally refreshed in-process index."""

    def __init__(
        self,
        files: WorkspaceFiles,
        *,
        max_files: int = 5_000,
        scan_file: Callable[[str], RepoFileFacts] | None = None,
    ) -> None:
        if not isinstance(files, WorkspaceFiles):
            raise TypeError("files must be WorkspaceFiles")
        if isinstance(max_files, bool) or not isinstance(max_files, int):
            raise TypeError("max_files must be an integer")
        if max_files <= 0:
            raise ValueError("max_files must be positive")
        if scan_file is not None and not callable(scan_file):
            raise TypeError("scan_file must be callable")
        self.files = files
        self.max_files = max_files
        self._scan_file = scan_file or RepoFileScanner(files).scan
        self._state_lock = RLock()
        self._update_lock = Lock()
        self._records: dict[str, RepoFileFacts] = {}
        self._snapshot = RepoIndexSnapshot(0)
        self._initialized = False
        self._dirty: set[str] = set()
        self._reconcile_requested = False

    def snapshot_for_turn(self) -> RepoIndexSnapshot:
        """Apply pending changes once, then return the stable current snapshot."""
        with self._update_lock:
            with self._state_lock:
                initialized = self._initialized
                dirty = tuple(sorted(self._dirty))
                reconcile = self._reconcile_requested
                if initialized and not dirty and not reconcile:
                    return self._snapshot
                self._dirty.difference_update(dirty)
                self._reconcile_requested = False
                current = dict(self._records)
            try:
                if not initialized:
                    updated = self._refresh(self._scan_all(), dirty)
                elif reconcile:
                    updated = self._refresh(
                        self._reconcile(current), dirty
                    )
                else:
                    updated = self._refresh(current, dirty)
            except Exception:
                with self._state_lock:
                    self._dirty.update(dirty)
                    self._reconcile_requested = (
                        self._reconcile_requested or reconcile
                    )
                raise
            with self._state_lock:
                changed = not self._initialized or updated != self._records
                if changed:
                    generation = self._snapshot.generation + 1
                    self._records = updated
                    self._snapshot = _publish_snapshot(
                        generation, updated
                    )
                self._initialized = True
                return self._snapshot

    def invalidate(self, paths: Sequence[str]) -> None:
        """Queue exact paths, or request one bounded reconciliation when empty."""
        if isinstance(paths, (str, bytes)) or not isinstance(paths, Sequence):
            raise TypeError("paths must be a sequence of strings")
        checked = tuple(paths)
        if any(not isinstance(path, str) or not path for path in checked):
            raise ValueError("paths must contain non-empty strings")
        if not checked:
            self.files.invalidate_inventory()
            with self._state_lock:
                self._reconcile_requested = True
            return
        normalized = tuple(self._normalize(path) for path in checked)
        with self._state_lock:
            self._dirty.update(normalized)

    def snapshot(self) -> RepoIndexSnapshot:
        """Return the last published generation without refreshing it."""
        with self._state_lock:
            return self._snapshot

    def _scan_all(self) -> dict[str, RepoFileFacts]:
        try:
            paths = self.files.list_files(
                max_entries=self.max_files,
                max_scanned_entries=max(1_000, self.max_files * 20),
            )
        except (OSError, WorkspaceError) as error:
            raise RepoMapError("bounded repository index scan failed") from error
        records: dict[str, RepoFileFacts] = {}
        for path in paths:
            facts = self._scan_current(path)
            if facts is not None:
                records[path] = facts
        return records

    def _refresh(
        self,
        records: dict[str, RepoFileFacts],
        dirty: Sequence[str],
    ) -> dict[str, RepoFileFacts]:
        updated = dict(records)
        for path in dirty:
            signature = self._current_signature(path)
            if signature is None or self.files.ignore.is_ignored(
                path, is_dir=False
            ):
                updated.pop(path, None)
                continue
            existing = updated.get(path)
            if existing is not None and existing.signature == signature:
                continue
            facts = self._scan_current(path)
            if facts is None:
                updated.pop(path, None)
            else:
                updated[path] = facts
        return self._bounded(updated, dirty)

    def _reconcile(
        self, records: dict[str, RepoFileFacts]
    ) -> dict[str, RepoFileFacts]:
        try:
            paths = self.files.list_files(
                max_entries=self.max_files,
                max_scanned_entries=max(1_000, self.max_files * 20),
            )
        except (OSError, WorkspaceError) as error:
            raise RepoMapError("bounded repository reconciliation failed") from error
        visible = set(paths)
        updated = {
            path: facts
            for path, facts in records.items()
            if path in visible
        }
        for path in paths:
            signature = self._current_signature(path)
            existing = updated.get(path)
            if (
                signature is not None
                and existing is not None
                and existing.signature == signature
            ):
                continue
            facts = self._scan_current(path)
            if facts is None:
                updated.pop(path, None)
            else:
                updated[path] = facts
        return updated

    def _scan_current(self, path: str) -> RepoFileFacts | None:
        try:
            facts = self._scan_file(path)
        except (OSError, WorkspaceError):
            return None
        if not isinstance(facts, RepoFileFacts):
            raise TypeError("scan_file must return RepoFileFacts")
        if facts.path != path:
            raise ValueError("scan_file returned facts for a different path")
        return facts

    def _current_signature(self, path: str) -> FileSignature | None:
        try:
            absolute = self.files.guard.resolve(path)
            metadata = absolute.stat()
            if not absolute.is_file():
                return None
            return FileSignature(metadata.st_size, metadata.st_mtime_ns)
        except (OSError, WorkspaceError):
            return None

    def _normalize(self, path: str) -> str:
        resolved = self.files.guard.resolve(path)
        return self.files.guard.relative(resolved).as_posix()

    def _bounded(
        self,
        records: dict[str, RepoFileFacts],
        preferred: Sequence[str],
    ) -> dict[str, RepoFileFacts]:
        if len(records) <= self.max_files:
            return records
        ordered = tuple(
            dict.fromkeys(
                path
                for path in (*preferred, *sorted(records))
                if path in records
            )
        )
        retained = ordered[: self.max_files]
        return {path: records[path] for path in retained}


def _publish_snapshot(
    generation: int, records: dict[str, RepoFileFacts]
) -> RepoIndexSnapshot:
    module_index = _module_index(records)
    entries = tuple(
        RepoEntry(
            path,
            records[path].symbols,
            _resolve_imports(path, records[path].imports, module_index),
            records[path].size_bytes,
        )
        for path in sorted(records, key=lambda item: (item.casefold(), item))
    )
    return RepoIndexSnapshot(generation, entries)


def _module_index(records: dict[str, RepoFileFacts]) -> dict[str, str]:
    index: dict[str, str] = {}
    for path in records:
        if not path.endswith(".py"):
            continue
        module = path[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        if module:
            index.setdefault(module, path)
    return index


def _resolve_imports(
    path: str,
    refs: Sequence[ImportRef],
    index: dict[str, str],
) -> tuple[str, ...]:
    current = path[:-3].replace("/", ".")
    package = (
        current[: -len(".__init__")]
        if current.endswith(".__init__")
        else current.rpartition(".")[0]
    )
    dependencies: set[str] = set()
    for ref in refs:
        if ref.level:
            parts = package.split(".") if package else []
            climb = ref.level - 1
            if climb > len(parts):
                continue
            prefix = parts[: len(parts) - climb]
            base = ".".join(
                (*prefix, *filter(None, ref.module.split(".")))
            )
        else:
            base = ref.module
        candidates = [
            f"{base}.{name}".strip(".") for name in ref.names
        ]
        candidates.append(base)
        for candidate in candidates:
            if candidate in index and index[candidate] != path:
                dependencies.add(index[candidate])
    return tuple(sorted(dependencies))
