from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from threading import RLock

from code_agent.workspace.errors import WorkspaceError
from code_agent.workspace.files import WorkspaceFiles

from .models import FileSignature
from .errors import RepoMapError
from .models import RepoEntry
from .repo_scan import RepoFileFacts, RepoFileScanner
from .repo_paths import canonical_repo_path
from .repo_search import RepoLexicalRanks, SQLiteRepoSearch
from .repo_search_documents import bound_search_facts
from .repo_semantic_graph import UnifiedSemanticGraph
from .repo_snapshot import publish_entries, strip_search_text


_MAX_SEARCH_BODY_BYTES = 16_000_000


@dataclass(frozen=True)
class RepoIndexSnapshot:
    """One immutable, internally consistent repository fact generation."""

    generation: int
    entries: tuple[RepoEntry, ...] = ()
    semantic_graph: UnifiedSemanticGraph = field(
        init=False, repr=False, compare=False
    )

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
        object.__setattr__(
            self, "semantic_graph", UnifiedSemanticGraph.from_entries(entries)
        )


class RepoIndexService:
    """Own one lazily initialized, incrementally refreshed in-process index."""

    def __init__(
        self,
        files: WorkspaceFiles,
        *,
        max_files: int = 5_000,
        scan_file: Callable[[str], RepoFileFacts] | None = None,
        search_index: SQLiteRepoSearch | None = None,
    ) -> None:
        if not isinstance(files, WorkspaceFiles):
            raise TypeError("files must be WorkspaceFiles")
        if isinstance(max_files, bool) or not isinstance(max_files, int):
            raise TypeError("max_files must be an integer")
        if max_files <= 0:
            raise ValueError("max_files must be positive")
        if scan_file is not None and not callable(scan_file):
            raise TypeError("scan_file must be callable")
        if search_index is not None and not isinstance(
            search_index, SQLiteRepoSearch
        ):
            raise TypeError("search_index must be a SQLiteRepoSearch")
        self.files = files
        self.max_files = max_files
        self._max_search_body_bytes = (
            _MAX_SEARCH_BODY_BYTES // max_files
        )
        self._scan_file = scan_file or RepoFileScanner(files).scan
        self._state_lock = RLock()
        self._update_lock = RLock()
        self._search_index = search_index or SQLiteRepoSearch()
        self._view_identity = object()
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
            self._search_index.sync(current, updated)
            indexed = strip_search_text(updated)
            with self._state_lock:
                changed = not self._initialized or indexed != self._records
                if changed:
                    generation = self._snapshot.generation + 1
                    self._records = indexed
                    self._snapshot = RepoIndexSnapshot(
                        generation,
                        publish_entries(indexed),
                    )
                self._initialized = True
                return self._snapshot

    def query_for_turn(
        self,
        query: str,
        *,
        limit: int = 64,
    ) -> tuple[RepoIndexSnapshot, RepoLexicalRanks]:
        """Refresh and query one generation while holding the update boundary."""
        if not isinstance(query, str):
            raise TypeError("query must be text")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._update_lock:
            snapshot = self.snapshot_for_turn()
            return snapshot, self._search_index.rank(query, limit=limit)

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

    @property
    def view_identity(self) -> object:
        """Return a cache token that cannot be reused during its lifetime."""
        return self._view_identity

    def close(self) -> None:
        """Release the owned in-memory lexical index."""
        with self._update_lock:
            self._search_index.close()

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
        return bound_search_facts(
            facts,
            self._max_search_body_bytes,
        )

    def _current_signature(self, path: str) -> FileSignature | None:
        try:
            absolute = self.files.guard.resolve(path)
            metadata = absolute.stat()
            if not absolute.is_file():
                return None
            return FileSignature.from_stat(metadata)
        except (OSError, WorkspaceError):
            return None

    def _normalize(self, path: str) -> str:
        resolved = self.files.guard.resolve(path)
        return canonical_repo_path(
            self.files.guard.relative(resolved).as_posix()
        )

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
