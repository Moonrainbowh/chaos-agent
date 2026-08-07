from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from math import sqrt

from .repo_query import quote_fts_term
from .repo_search_documents import SearchDocument


_RRF_K = 60


def delete_search_path(
    connection: sqlite3.Connection,
    table: str,
    path: str,
) -> None:
    connection.execute(f"DELETE FROM {table} WHERE identity = ?", (path,))


def insert_search_document(
    connection: sqlite3.Connection,
    table: str,
    document: SearchDocument,
) -> None:
    connection.execute(
        f"INSERT INTO {table}("
        "identity, path_terms, symbols, aux_terms, contract_terms, body"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        document,
    )


def match_terms(
    connection: sqlite3.Connection,
    table: str,
    terms: Sequence[str],
    limit: int,
) -> tuple[str, ...]:
    if not terms:
        return ()
    expression = " OR ".join(quote_fts_term(term) for term in terms)
    rows = connection.execute(
        f"SELECT identity, "
        f"bm25({table}, 0.0, 5.0, 3.0, 1.5, 2.0, 1.0) score "
        f"FROM {table} WHERE {table} MATCH ? "
        "ORDER BY score, identity COLLATE NOCASE, identity LIMIT ?",
        (expression, limit),
    )
    return tuple(str(row[0]) for row in rows)


def match_individual_terms(
    connection: sqlite3.Connection,
    table: str,
    terms: Sequence[str],
    limit: int,
    *,
    column: str | None = None,
) -> tuple[str, ...]:
    channels: list[tuple[tuple[str, ...], float]] = []
    for term in terms:
        matches = _match_column(
            connection,
            table,
            column,
            term,
            limit,
        )
        if matches:
            channels.append((matches, 1.0 / sqrt(len(matches))))
    return merge_ranked_paths(*channels)[:limit]


def merge_ranked_paths(
    *channels: tuple[Sequence[str], float],
) -> tuple[str, ...]:
    scores: dict[str, float] = {}
    for paths, weight in channels:
        for rank, path in enumerate(dict.fromkeys(paths), start=1):
            scores[path] = scores.get(path, 0.0) + weight / (_RRF_K + rank)
    return tuple(
        sorted(
            scores,
            key=lambda path: (-scores[path], path.casefold(), path),
        )
    )


def _match_column(
    connection: sqlite3.Connection,
    table: str,
    column: str | None,
    term: str,
    limit: int,
) -> tuple[str, ...]:
    literal = quote_fts_term(term)
    expression = (
        literal
        if column is None
        else f"{column} : {literal}"
    )
    rows = connection.execute(
        f"SELECT identity FROM {table} WHERE {table} MATCH ? "
        "ORDER BY rank, identity COLLATE NOCASE, identity LIMIT ?",
        (expression, limit),
    )
    return tuple(str(row[0]) for row in rows)
