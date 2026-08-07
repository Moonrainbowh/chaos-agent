from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from threading import RLock

from code_agent.workspace.files import WorkspaceFiles

from .cache import RepoMapCache
from .models import ContextConfig, RepoEntry
from .repo_index import RepoIndexService, RepoIndexSnapshot
from .repo_query import bound_repo_query
from .repo_ranking import rank_repo_entries
from .repo_scan import RepoFileFacts, RepoFileScanner
from .repo_search import RepoLexicalRanks
from .tokens import estimate_tokens, truncate_to_tokens


@dataclass(frozen=True)
class _RepoViewKey:
    index_identity: object
    generation: int
    backend_key: str
    query: str
    touched_files: tuple[str, ...]
    token_budget: int


class RepoMapViewCache:
    """A bounded LRU of rendered views keyed by immutable index generation."""

    def __init__(self, *, max_entries: int = 64) -> None:
        if isinstance(max_entries, bool) or not isinstance(max_entries, int):
            raise TypeError("max_entries must be an integer")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self._entries: OrderedDict[_RepoViewKey, str] = OrderedDict()
        self._lock = RLock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get_or_build(
        self,
        key: _RepoViewKey,
        build: Callable[[], str],
    ) -> tuple[str, int, int]:
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached, 1, 0
            rendered = build()
            self._entries[key] = rendered
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            return rendered, 0, 1

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class RepoMapViewBuilder:
    """Rank and render one lightweight view from an immutable index snapshot."""

    def build(
        self,
        snapshot: RepoIndexSnapshot,
        query: str = "",
        touched_files: Sequence[str] = (),
        lexical: RepoLexicalRanks | None = None,
    ) -> tuple[RepoEntry, ...]:
        if not isinstance(snapshot, RepoIndexSnapshot):
            raise TypeError("snapshot must be a RepoIndexSnapshot")
        if not isinstance(query, str):
            raise TypeError("query must be text")
        if any(not isinstance(path, str) or not path for path in touched_files):
            raise ValueError("touched_files must contain non-empty paths")
        return rank_repo_entries(
            snapshot.entries,
            query,
            touched_files,
            lexical,
        )

    def render(
        self,
        snapshot: RepoIndexSnapshot,
        query: str,
        touched_files: Sequence[str],
        token_budget: int,
        lexical: RepoLexicalRanks | None = None,
    ) -> str:
        if isinstance(token_budget, bool) or not isinstance(token_budget, int):
            raise TypeError("token_budget must be an integer")
        if token_budget < 0:
            raise ValueError("token_budget must not be negative")
        if token_budget == 0:
            return ""
        chunks: list[str] = []
        used = 0
        for entry in self.build(snapshot, query, touched_files, lexical):
            chunk = _render_entry(entry)
            separator = "\n" if chunks else ""
            cost = estimate_tokens(separator + chunk)
            if used + cost <= token_budget:
                chunks.append(chunk)
                used += cost
                continue
            if not chunks:
                return truncate_to_tokens(chunk, token_budget)
            break
        return truncate_to_tokens("\n".join(chunks), token_budget)


class RepoMapBuilder:
    """Compatibility facade over a shared index and a pure per-turn view."""

    def __init__(
        self,
        files: WorkspaceFiles,
        config: ContextConfig,
        *,
        cache: RepoMapCache | None = None,
        index: RepoIndexService | None = None,
        view_cache: RepoMapViewCache | None = None,
    ) -> None:
        if not isinstance(files, WorkspaceFiles):
            raise TypeError("files must be WorkspaceFiles")
        if not isinstance(config, ContextConfig):
            raise TypeError("config must be a ContextConfig")
        if files.guard.root != config.workspace_root:
            raise ValueError(
                "workspace files root must match config.workspace_root"
            )
        if index is not None and not isinstance(index, RepoIndexService):
            raise TypeError("index must be a RepoIndexService")
        if index is not None and index.files is not files:
            raise ValueError("index and repo map must share WorkspaceFiles")
        if view_cache is not None and not isinstance(
            view_cache, RepoMapViewCache
        ):
            raise TypeError("view_cache must be a RepoMapViewCache")
        self.files = files
        self.config = config
        self.cache = cache or RepoMapCache(config.workspace_root)
        self._file_scanner: RepoFileScanner | None = None
        if index is None:
            self._file_scanner = RepoFileScanner(files)
            index = RepoIndexService(
                files,
                max_files=config.repo_scan,
                scan_file=lambda path: self._scan_file(path),
            )
        self.index = index
        self.view = RepoMapViewBuilder()
        self.view_cache = (
            view_cache if view_cache is not None else RepoMapViewCache()
        )

    def build(
        self, query: str = "", touched_files: Sequence[str] = ()
    ) -> tuple[RepoEntry, ...]:
        snapshot, lexical = self.index.query_for_turn(query)
        return self.view.build(snapshot, query, touched_files, lexical)

    def render(
        self,
        query: str,
        touched_files: Sequence[str],
        token_budget: int,
    ) -> str:
        rendered, _, _ = self.render_with_metrics(
            query, touched_files, token_budget
        )
        return rendered

    def render_with_metrics(
        self,
        query: str,
        touched_files: Sequence[str],
        token_budget: int,
    ) -> tuple[str, int, int]:
        if not isinstance(query, str):
            raise TypeError("query must be text")
        checked_touched = tuple(touched_files)
        if any(
            not isinstance(path, str) or not path
            for path in checked_touched
        ):
            raise ValueError("touched_files must contain non-empty paths")
        if isinstance(token_budget, bool) or not isinstance(token_budget, int):
            raise TypeError("token_budget must be an integer")
        if token_budget < 0:
            raise ValueError("token_budget must not be negative")
        if token_budget == 0:
            return "", 0, 0
        snapshot, lexical = self.index.query_for_turn(query)
        key = _RepoViewKey(
            self.index.view_identity,
            snapshot.generation,
            lexical.backend_key,
            bound_repo_query(query),
            _normalize_touched(checked_touched),
            token_budget,
        )
        return self.view_cache.get_or_build(
            key,
            lambda: self.view.render(
                snapshot,
                query,
                checked_touched,
                token_budget,
                lexical,
            ),
        )

    def invalidate(self, paths: Sequence[str]) -> None:
        self.index.invalidate(paths)

    def _scan_file(self, path: str) -> RepoFileFacts:
        if self._file_scanner is None:
            raise RuntimeError("shared index owns file scanning")
        return self._file_scanner.scan(path)


def _normalize_touched(paths: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                path.replace("\\", "/")
                .removeprefix("./")
                .casefold()
                for path in paths
            }
        )
    )


def _render_entry(entry: RepoEntry) -> str:
    lines = [entry.path]
    if entry.symbols:
        lines.append(
            "  "
            + ", ".join(
                f"{item.kind}:{item.name}:{item.line}"
                for item in entry.symbols
            )
        )
    if entry.dependencies:
        lines.append("  deps:" + ",".join(entry.dependencies))
    return "\n".join(lines)
