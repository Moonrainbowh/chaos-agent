from __future__ import annotations

import re
from collections.abc import Sequence

from .models import RepoEntry
from .repo_query import bound_repo_query
from .repo_search import RepoLexicalRanks


_QUERY_TOKEN = re.compile(r"[\w.-]+", re.UNICODE)
_RRF_K = 60
_MAX_CONTRACT_EXPANSIONS = 1
_SOURCE_SUFFIXES = frozenset(
    {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".cs"}
)


def rank_repo_entries(
    entries: Sequence[RepoEntry],
    query: str,
    touched_files: Sequence[str],
    lexical: RepoLexicalRanks | None = None,
) -> tuple[RepoEntry, ...]:
    """Fuse bounded lexical, structural, graph, and task-local ranks."""
    checked_entries, checked_touched = _validate_rank_inputs(
        entries, query, touched_files, lexical
    )
    scores = {entry.path: 0.0 for entry in checked_entries}
    for path in _touched_paths(checked_entries, checked_touched):
        scores[path] += 8.0 / (_RRF_K + 1)
    lexical_ranks = lexical or RepoLexicalRanks()
    channels = (
        (_structured_paths(checked_entries, query), 3.0, False),
        (lexical_ranks.term_paths, 2.0, True),
        (lexical_ranks.trigram_paths, 1.5, True),
        (lexical_ranks.short_paths, 1.5, True),
        (lexical_ranks.contract_paths, 2.0, True),
        (
            _contract_scope_paths(checked_entries, lexical_ranks),
            2.0,
            False,
        ),
        (_dependency_paths(checked_entries), 0.5, False),
    )
    for paths, weight, lexical_channel in channels:
        _add_rank_channel(scores, paths, weight, lexical_channel)
    return tuple(
        sorted(
            checked_entries,
            key=lambda entry: (
                -scores[entry.path],
                entry.path.casefold(),
                entry.path,
            ),
        )
    )


def _validate_rank_inputs(
    entries: Sequence[RepoEntry],
    query: str,
    touched_files: Sequence[str],
    lexical: RepoLexicalRanks | None,
) -> tuple[tuple[RepoEntry, ...], tuple[str, ...]]:
    if not isinstance(query, str):
        raise TypeError("query must be text")
    checked_entries = tuple(entries)
    if not all(isinstance(entry, RepoEntry) for entry in checked_entries):
        raise TypeError("entries must contain RepoEntry values")
    checked_touched = tuple(touched_files)
    if any(
        not isinstance(path, str) or not path
        for path in checked_touched
    ):
        raise ValueError("touched_files must contain non-empty paths")
    if lexical is not None and not isinstance(lexical, RepoLexicalRanks):
        raise TypeError("lexical must be RepoLexicalRanks or None")
    return checked_entries, checked_touched


def _add_rank_channel(
    scores: dict[str, float],
    paths: Sequence[str],
    weight: float,
    lexical_channel: bool,
) -> None:
    for rank, path in enumerate(dict.fromkeys(paths), start=1):
        if path not in scores:
            continue
        path_weight = (
            weight * _lexical_path_prior(path)
            if lexical_channel
            else weight
        )
        scores[path] += path_weight / (_RRF_K + rank)


def _touched_paths(
    entries: Sequence[RepoEntry],
    touched_files: Sequence[str],
) -> tuple[str, ...]:
    by_normalized = {
        _normalize_path(entry.path): entry.path for entry in entries
    }
    ranked: list[str] = []
    for path in touched_files:
        matched = by_normalized.get(_normalize_path(path))
        if matched is not None and matched not in ranked:
            ranked.append(matched)
    return tuple(ranked)


def _structured_paths(
    entries: Sequence[RepoEntry], query: str
) -> tuple[str, ...]:
    bounded = bound_repo_query(query).casefold()
    tokens = tuple(dict.fromkeys(_QUERY_TOKEN.findall(bounded)))
    if not tokens:
        return ()
    scored: list[tuple[int, RepoEntry]] = []
    for entry in entries:
        path = entry.path.casefold()
        score = 0
        for token in tokens:
            if token == path:
                score += 30
            elif token in path:
                score += 12
            for symbol in entry.symbols:
                name = symbol.name.casefold()
                score += (
                    35
                    if token == name
                    else 20
                    if token in name
                    else 0
                )
        if score:
            scored.append((score, entry))
    return tuple(
        item.path
        for _, item in sorted(
            scored,
            key=lambda pair: (
                -pair[0],
                pair[1].path.casefold(),
                pair[1].path,
            ),
        )
    )


def _dependency_paths(
    entries: Sequence[RepoEntry],
) -> tuple[str, ...]:
    indegree = {entry.path: 0 for entry in entries}
    for entry in entries:
        for dependency in entry.dependencies:
            if dependency in indegree:
                indegree[dependency] += 1
    return tuple(
        path
        for path in sorted(
            indegree,
            key=lambda path: (
                -indegree[path],
                path.casefold(),
                path,
            ),
        )
        if indegree[path] > 0
    )


def _contract_scope_paths(
    entries: Sequence[RepoEntry],
    lexical: RepoLexicalRanks,
) -> tuple[str, ...]:
    ranked: list[str] = []
    for contract in lexical.contract_paths[:_MAX_CONTRACT_EXPANSIONS]:
        normalized = _normalize_path(contract)
        if not normalized.endswith("/agents.md"):
            continue
        prefix = normalized[: -len("agents.md")]
        for entry in entries:
            path = _normalize_path(entry.path)
            suffix = "." + path.rpartition(".")[2] if "." in path else ""
            if (
                path.startswith(prefix)
                and path != normalized
                and suffix in _SOURCE_SUFFIXES
                and not _is_test_path(path)
                and entry.path not in ranked
            ):
                ranked.append(entry.path)
    return tuple(ranked[:64])


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./").casefold()


def _is_test_path(path: str) -> bool:
    normalized = f"/{_normalize_path(path)}"
    name = normalized.rsplit("/", 1)[-1]
    return "/tests/" in normalized or name.startswith("test_")


def _lexical_path_prior(path: str) -> float:
    normalized = _normalize_path(path)
    if _is_test_path(normalized):
        return 0.25
    if normalized.startswith("docs/"):
        return 0.3
    if normalized.endswith("/agents.md") or normalized == "agents.md":
        return 0.35
    if normalized.endswith(".md"):
        return 0.5
    return 1.0
