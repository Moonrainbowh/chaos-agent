from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import RLock

from .repo_query import contract_query_terms, plan_repo_query
from .repo_scan import RepoFileFacts
from .repo_search_documents import search_document
from .repo_search_sql import (
    delete_search_path,
    insert_search_document,
    match_individual_terms,
    match_terms,
    merge_ranked_paths,
)


_MAX_RESULTS = 64


@dataclass(frozen=True)
class RepoLexicalRanks:
    """Bounded file ranks from independent lexical retrieval channels."""

    term_paths: tuple[str, ...] = ()
    trigram_paths: tuple[str, ...] = ()
    short_paths: tuple[str, ...] = ()
    contract_paths: tuple[str, ...] = ()
    backend_key: str = "structured"

    def __post_init__(self) -> None:
        for label in (
            "term_paths",
            "trigram_paths",
            "short_paths",
            "contract_paths",
        ):
            paths = tuple(getattr(self, label))
            if not all(isinstance(path, str) and path for path in paths):
                raise ValueError(f"{label} must contain non-empty paths")
            object.__setattr__(self, label, paths)
        if not isinstance(self.backend_key, str) or not self.backend_key:
            raise ValueError("backend_key must be non-empty text")

    @property
    def paths(self) -> tuple[str, ...]:
        return merge_ranked_paths(
            (self.term_paths, 1.0),
            (self.trigram_paths, 0.75),
            (self.short_paths, 0.75),
            (self.contract_paths, 0.9),
        )

class SQLiteRepoSearch:
    """Maintain a guarded, in-memory FTS5 index for repository file ranking."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection] | None = None,
    ) -> None:
        if connection_factory is not None and not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._factory = connection_factory or _memory_connection
        self._lock = RLock()
        self._connection: sqlite3.Connection | None = None
        self._has_trigram = False
        self._serial = 0
        self._initialize()

    @property
    def available(self) -> bool:
        with self._lock:
            return self._connection is not None

    @property
    def backend_key(self) -> str:
        with self._lock:
            if self._connection is None:
                return f"structured:{self._serial}"
            return f"fts5:{int(self._has_trigram)}:{self._serial}"

    def sync(
        self,
        previous: Mapping[str, RepoFileFacts],
        current: Mapping[str, RepoFileFacts],
    ) -> None:
        """Apply only added, changed, and removed documents in one transaction."""
        with self._lock:
            connection = self._connection
            if connection is None:
                return
            removed = tuple(sorted(set(previous) - set(current)))
            changed = tuple(
                current[path]
                for path in sorted(current)
                if path not in previous
                or previous[path].signature != current[path].signature
            )
            if not removed and not changed:
                return
            try:
                connection.execute("BEGIN")
                for path in (*removed, *(item.path for item in changed)):
                    delete_search_path(connection, "repo_terms", path)
                    if self._has_trigram:
                        delete_search_path(connection, "repo_grams", path)
                for facts in changed:
                    document = search_document(facts)
                    terms_document = (
                        (*document[:5], "")
                        if self._has_trigram
                        else document
                    )
                    insert_search_document(
                        connection, "repo_terms", terms_document
                    )
                    if self._has_trigram:
                        insert_search_document(
                            connection, "repo_grams", document
                        )
                connection.commit()
            except sqlite3.Error:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
                self._disable_locked()

    def rank(self, query: str, *, limit: int = _MAX_RESULTS) -> RepoLexicalRanks:
        if not isinstance(query, str):
            raise TypeError("query must be text")
        bounded_limit = _bounded_result_limit(limit)
        with self._lock:
            connection = self._connection
            backend_key = self.backend_key
            if connection is None:
                return RepoLexicalRanks(backend_key=backend_key)
            plan = plan_repo_query(query)
            try:
                terms = match_terms(
                    connection, "repo_terms", plan.terms, bounded_limit
                )
                trigrams = (
                    match_terms(
                        connection,
                        "repo_grams",
                        plan.trigrams,
                        bounded_limit,
                    )
                    if self._has_trigram
                    else ()
                )
                shorts = match_individual_terms(
                    connection,
                    "repo_terms",
                    plan.shorts,
                    bounded_limit,
                )
                contracts = match_individual_terms(
                    connection,
                    "repo_terms",
                    contract_query_terms(plan),
                    bounded_limit,
                    column="contract_terms",
                )
            except sqlite3.Error:
                self._disable_locked()
                return RepoLexicalRanks(backend_key=self.backend_key)
            return RepoLexicalRanks(
                term_paths=terms,
                trigram_paths=trigrams,
                short_paths=shorts,
                contract_paths=contracts,
                backend_key=backend_key,
            )

    def close(self) -> None:
        with self._lock:
            self._disable_locked()

    def _initialize(self) -> None:
        with self._lock:
            try:
                connection = self._factory()
                connection.execute(
                    "CREATE VIRTUAL TABLE repo_terms USING fts5("
                    "identity UNINDEXED, path_terms, symbols, aux_terms, "
                    "contract_terms, body, "
                    "tokenize='unicode61 remove_diacritics 2')"
                )
                connection.commit()
            except sqlite3.Error:
                try:
                    connection.close()  # type: ignore[possibly-undefined]
                except (NameError, sqlite3.Error):
                    pass
                self._serial += 1
                return
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE repo_grams USING fts5("
                    "identity UNINDEXED, path_terms, symbols, aux_terms, "
                    "contract_terms, body, "
                    "tokenize='trigram')"
                )
                connection.commit()
                self._has_trigram = True
            except sqlite3.Error:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
                self._has_trigram = False
            self._connection = connection
            self._serial += 1

    def _disable_locked(self) -> None:
        connection = self._connection
        self._connection = None
        self._has_trigram = False
        self._serial += 1
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass


def _memory_connection() -> sqlite3.Connection:
    return sqlite3.connect(":memory:", check_same_thread=False)


def _bounded_result_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit must be an integer")
    if limit <= 0:
        raise ValueError("limit must be positive")
    return min(limit, _MAX_RESULTS)
