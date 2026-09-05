from __future__ import annotations

import re
from collections import deque
from collections.abc import Sequence

from code_agent.context.repo_semantic_graph import UnifiedSemanticGraph

from .models import InsightItem
from .inputs import in_scope


_TOKEN = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+")
_ENTRYPOINTS = frozenset(
    {"__init__.py", "__main__.py", "main.py", "app.py", "cli.py", "setup.py"}
)


def rank_context_nodes(
    graph: UnifiedSemanticGraph,
    query: str,
    limit: int,
    lexical_paths: Sequence[str] = (),
) -> tuple[InsightItem, ...]:
    seeds = _query_scores(graph, query, lexical_paths)
    expanded = dict(seeds)
    for path, (score, reason) in tuple(seeds.items())[:6]:
        for neighbor in graph.get_dependencies(path):
            _raise_score(expanded, neighbor, score - 24, f"dependency of {path}")
        for neighbor in graph.get_dependents(path):
            _raise_score(expanded, neighbor, score - 20, f"consumer of {path}")
    return _items(graph, expanded, limit, "heuristic")


def rank_bug_locations(
    graph: UnifiedSemanticGraph,
    query: str,
    limit: int,
    lexical_paths: Sequence[str] = (),
) -> tuple[InsightItem, ...]:
    seeds = _query_scores(graph, query, lexical_paths)
    ranked = {
        path: (score + _bug_path_bias(path), reason)
        for path, (score, reason) in seeds.items()
    }
    for path, (score, _) in tuple(seeds.items())[:6]:
        for caller in graph.get_dependents(path):
            _raise_score(ranked, caller, score - 15, f"caller/consumer of {path}")
        for dependency in graph.get_dependencies(path):
            _raise_score(ranked, dependency, score - 22, f"dependency of {path}")
    return _items(graph, ranked, limit, "heuristic")


def rank_impacted_tests(
    graph: UnifiedSemanticGraph, changed: Sequence[str], limit: int
) -> tuple[InsightItem, ...]:
    values: list[InsightItem] = []
    distances = reverse_distances(graph, changed)
    for path in graph.find_impacted_tests(changed, max_depth=len(graph.nodes) + 1):
        distance = distances.get(path)
        score = max(10, 100 - distance * 5) if distance is not None else 0
        detail = (
            f"reverse dependency distance {distance}"
            if distance is not None
            else "matched by repository test convention"
        )
        values.append(InsightItem(path, detail, score, "static"))
    return tuple(sorted(values, key=_item_key)[:limit])


def dead_code_candidates(
    graph: UnifiedSemanticGraph, prefix: str, limit: int
) -> tuple[InsightItem, ...]:
    normalized = prefix.replace("\\", "/").strip("/ ").casefold()
    values = _leaf_module_candidates(graph, normalized)
    if len(values) < limit:
        values.extend(_private_symbol_candidates(graph, normalized, limit - len(values)))
    return tuple(values[:limit])


def _query_scores(
    graph: UnifiedSemanticGraph,
    query: str,
    lexical_paths: Sequence[str],
) -> dict[str, tuple[int, str]]:
    folded = query.casefold().strip()
    tokens = set(_TOKEN.findall(folded))
    scores: dict[str, tuple[int, str]] = {}
    for path in graph.nodes:
        symbols = tuple(graph.symbols.get(path, ()))
        haystack = " ".join((path, *symbols)).casefold()
        overlap = sum(token in haystack for token in tokens)
        exact = bool(folded and folded in haystack)
        if not exact and overlap == 0:
            continue
        centrality = min(20, len(graph.get_dependents(path)) * 2)
        score = (80 if exact else 0) + overlap * 25 + centrality
        reason = "exact text match" if exact else f"matched {overlap} query term(s)"
        scores[path] = (score, reason)
    nodes = set(graph.nodes)
    for index, path in enumerate(lexical_paths[:64]):
        if path not in nodes:
            continue
        _raise_score(scores, path, max(25, 75 - index), "lexical source match")
    return dict(sorted(scores.items(), key=lambda item: (-item[1][0], item[0])))


def _items(
    graph: UnifiedSemanticGraph,
    scores: dict[str, tuple[int, str]],
    limit: int,
    confidence: str,
) -> tuple[InsightItem, ...]:
    ranked = sorted(scores.items(), key=lambda item: (-item[1][0], item[0]))
    return tuple(
        InsightItem(
            path,
            f"{reason}; {len(graph.get_dependencies(path))} deps, "
            f"{len(graph.get_dependents(path))} consumers",
            score,
            confidence,
        )
        for path, (score, reason) in ranked[:limit]
    )


def _raise_score(
    scores: dict[str, tuple[int, str]], path: str, score: int, reason: str
) -> None:
    current = scores.get(path)
    if score > 0 and (current is None or score > current[0]):
        scores[path] = (score, reason)


def reverse_distances(
    graph: UnifiedSemanticGraph, starts: Sequence[str]
) -> dict[str, int]:
    queue = deque((path, 0) for path in starts)
    distances = {path: 0 for path in starts}
    while queue:
        current, depth = queue.popleft()
        for dependent in graph.get_dependents(current):
            if dependent not in distances:
                distances[dependent] = depth + 1
                queue.append((dependent, depth + 1))
    return distances


def _leaf_module_candidates(
    graph: UnifiedSemanticGraph, prefix: str
) -> list[InsightItem]:
    values: list[InsightItem] = []
    for path in graph.nodes:
        name = path.rsplit("/", 1)[-1]
        if not path.endswith(".py") or _is_test(path) or name in _ENTRYPOINTS:
            continue
        if not in_scope(path, prefix):
            continue
        if graph.get_dependents(path):
            continue
        values.append(
            InsightItem(
                path,
                f"no static consumers; imports {len(graph.get_dependencies(path))} module(s)",
                40,
                "candidate",
            )
        )
    return values


def _private_symbol_candidates(
    graph: UnifiedSemanticGraph, prefix: str, limit: int
) -> list[InsightItem]:
    inbound = {
        (relation.target_path, relation.target_symbol)
        for relations in graph.relations.values()
        for relation in relations
        if relation.target_path and relation.target_symbol
    }
    values: list[InsightItem] = []
    for path in graph.nodes:
        if _is_test(path) or not in_scope(path, prefix):
            continue
        for name, symbol in graph.symbols.get(path, {}).items():
            leaf = name.rsplit(".", 1)[-1]
            if not leaf.startswith("_") or leaf.startswith("__"):
                continue
            if (path, name) in inbound or (path, leaf) in inbound:
                continue
            values.append(
                InsightItem(
                    f"{path}:{symbol.line} · {name}",
                    "private symbol has no resolved static references",
                    20,
                    "candidate",
                )
            )
            if len(values) >= limit:
                return values
    return values


def _is_test(path: str) -> bool:
    parts = path.casefold().split("/")
    name = parts[-1]
    return "tests" in parts or name.startswith("test_") or name.endswith("_test.py")


def _bug_path_bias(path: str) -> int:
    folded = path.casefold()
    if folded.endswith(("agents.md", "readme.md", ".rst")):
        return -25
    if _is_test(path):
        return -10
    if folded.startswith("src/") and folded.endswith(".py"):
        return 20
    return 0


def _item_key(item: InsightItem) -> tuple[int, str]:
    return (-(item.score or 0), item.label)
