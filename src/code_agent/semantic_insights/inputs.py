from __future__ import annotations

from collections.abc import Sequence

from code_agent.context.repo_semantic_graph import UnifiedSemanticGraph


def arguments(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("arguments must be a sequence of strings")
    raw = tuple(values)
    if not all(isinstance(value, str) for value in raw):
        raise TypeError("arguments must contain strings")
    checked = raw
    if len(checked) > 256 or sum(map(len, checked)) > 4096:
        raise ValueError("arguments exceed 256 values or 4096 characters")
    if any(not value.strip() or any(ord(c) < 32 for c in value) for value in checked):
        raise ValueError("arguments must contain non-empty text")
    return checked


def lexical_paths(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("lexical_paths must be a sequence of strings")
    checked = tuple(values)
    if not all(isinstance(value, str) and value for value in checked):
        raise ValueError("lexical_paths must contain non-empty paths")
    return checked[:64]


def required_query(values: tuple[str, ...], label: str) -> str:
    if not values:
        raise ValueError(f"{label} is required")
    return " ".join(values)


def resolve_paths(
    graph: UnifiedSemanticGraph, values: tuple[str, ...], limit: int = 256
) -> tuple[str, ...]:
    if not values:
        raise ValueError("at least one repository path is required")
    resolved: list[str] = []
    for value in values:
        matches = scope_paths(graph, (value,))
        for path in matches:
            if path not in resolved:
                resolved.append(path)
    if not resolved:
        raise ValueError("no indexed files in requested scope")
    if len(resolved) > limit:
        raise ValueError(f"scope expands to {len(resolved)} files; narrow to at most {limit}")
    return tuple(resolved)


def scope_paths(graph: UnifiedSemanticGraph, values: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve one exact file/directory scope without confusing adjacent prefixes."""
    if len(values) > 1:
        raise ValueError("use one file/directory scope; quote paths containing spaces")
    query = values[0].replace("\\", "/") if values else "."
    if query.startswith("/") or ":" in query or ".." in query.split("/"):
        raise ValueError("scope must be repository-relative without parent traversal")
    query = query.removeprefix("./").rstrip("/")
    if query in {"", "."}:
        return tuple(graph.nodes)
    exact = tuple(p for p in graph.nodes if p == query or p.startswith(query + "/"))
    if exact:
        return exact
    matches = tuple(p for p in graph.nodes if in_scope(p, query))
    roots = {p[:len(query)] for p in matches}
    if len(roots) > 1:
        raise ValueError("ambiguous path casing; specify the exact indexed path")
    if not matches:
        raise ValueError(f"path is not present in semantic generation: {query}")
    return matches


def in_scope(path: str, prefix: str) -> bool:
    path, prefix = path.casefold(), prefix.casefold()
    return not prefix or path == prefix or path.startswith(prefix + "/")
