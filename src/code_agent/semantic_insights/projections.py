from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
import heapq

from code_agent.context.repo_semantic_graph import UnifiedSemanticGraph

from .inputs import scope_paths
from .models import InsightItem, InsightSection
from .ranking import reverse_distances


def repository_sections(
    graph: UnifiedSemanticGraph, arguments: tuple[str, ...]
) -> tuple[InsightSection, ...]:
    """Expose file/symbol drill-down even for repositories without dependency hubs."""
    nodes = scope_paths(graph, arguments)
    prefix = arguments[0] if arguments else "."
    relations = sum(len(graph.relations.get(path, ())) for path in nodes)
    inventory = InsightSection("Repository", (InsightItem(
        prefix, f"{len(nodes)} files · {relations} resolved relations · "
        f"{len(graph.nodes)} indexed files total", confidence="exact",
    ),))
    groups = Counter(path.rsplit("/", 1)[0] if "/" in path else "." for path in nodes)
    directories = InsightSection("Directory map", tuple(
        InsightItem(name, f"{count} indexed files", confidence="exact")
        for name, count in sorted(groups.items())
    ))
    files = InsightSection("Files", tuple(
        InsightItem(path, f"{len(graph.symbols.get(path, {}))} symbols · "
                    f"{len(graph.get_dependencies(path))} deps · "
                    f"{len(graph.get_dependents(path))} consumers") for path in nodes
    ))
    hubs = InsightSection("Dependency hubs", tuple(
        InsightItem(path, "direct static consumers", len(graph.get_dependents(path)))
        for path in sorted(nodes, key=lambda p: (-len(graph.get_dependents(p)), p))
        if graph.get_dependents(path)
    ))
    if len(nodes) != 1:
        return inventory, directories, files, hubs
    path = nodes[0]
    symbols = InsightSection("Symbols", tuple(
        InsightItem(f"{path}:{symbol.line} · {name}", symbol.kind, confidence="static")
        for name, symbol in graph.symbols.get(path, {}).items()
    ))
    neighbors = InsightSection("Relationships", tuple(
        InsightItem(p, reason) for reason, paths in (
            ("dependency", graph.get_dependencies(path)),
            ("consumer", graph.get_dependents(path)),
        ) for p in paths
    ))
    return inventory, files, symbols, neighbors


def refactor_sections(
    graph: UnifiedSemanticGraph, changed: Sequence[str]
) -> tuple[InsightSection, ...]:
    """Plan the full in-graph impact before display truncation; cycles remain explicit."""
    downstream = set(reverse_distances(graph, changed))
    scope = set(downstream)
    for path in changed:
        scope.update(graph.get_transitive_dependencies((path,), max_depth=len(graph.nodes) + 1))
    prerequisites = {
        path: set(graph.get_dependencies(path)).intersection(scope) for path in scope
    }
    ready = sorted(path for path, deps in prerequisites.items() if not deps)
    ordered: list[str] = []
    while ready:
        current = heapq.heappop(ready)
        ordered.append(current)
        for consumer in graph.get_dependents(current):
            if consumer in prerequisites and current in prerequisites[consumer]:
                prerequisites[consumer].remove(current)
                if not prerequisites[consumer]:
                    heapq.heappush(ready, consumer)
    unresolved = sorted(scope.difference(ordered))
    items = tuple(InsightItem(path, f"step {index}: dependencies before consumers")
                  for index, path in enumerate(ordered, 1))
    sections = [InsightSection("Refactor order", items)]
    if unresolved:
        sections.append(InsightSection("Cycle-dependent group (no safe linear order)", tuple(
            InsightItem(path, "cycle member or blocked by a cycle; review together")
            for path in unresolved
        )))
    sections.append(InsightSection("Original targets", tuple(
        InsightItem(path, "explicit refactor target") for path in changed
    )))
    return tuple(sections)
