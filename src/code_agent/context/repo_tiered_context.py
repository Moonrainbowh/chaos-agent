from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from collections.abc import Sequence

from .models import FileSignature, RepoEntry, RepoRelation, Symbol
from .repo_index import RepoIndexSnapshot
from .repo_paths import canonical_path_key
from .tokens import estimate_tokens, truncate_to_tokens


_PATH_LINE = re.compile(r"(?P<path>[^\r\n:\"']+?\.py):(?P<line>[1-9][0-9]*)")
_TEST_LIMIT = 16
TOKENIZER_VERSION = "estimate_tokens:v1"


@dataclass(frozen=True)
class TierNode:
    path: str
    kind: str
    start_line: int
    end_line: int
    symbol: str = ""
    signature: FileSignature | None = None
    skeleton: str = ""
    docstring: str = ""
    reasons: tuple[str, ...] = field(default_factory=tuple)
    resolution: str = "exact"
    distance: int = 0


@dataclass(frozen=True)
class TierSelection:
    generation: int
    tokenizer_version: str
    l0: tuple[TierNode, ...] = ()
    l1: tuple[TierNode, ...] = ()
    l2: tuple[TierNode, ...] = ()
    overflow_targets: tuple[TierNode, ...] = ()


def select_tiered_context(
    snapshot: RepoIndexSnapshot,
    ranked: Sequence[RepoEntry],
    query: str,
    touched_files: Sequence[str],
    token_budget: int,
) -> TierSelection:
    """Build a deterministic request-derived tier selection."""
    if not isinstance(snapshot, RepoIndexSnapshot):
        raise TypeError("snapshot must be a RepoIndexSnapshot")
    if not isinstance(query, str):
        raise TypeError("query must be text")
    if isinstance(token_budget, bool) or not isinstance(token_budget, int):
        raise TypeError("token_budget must be an integer")
    if token_budget < 0:
        raise ValueError("token_budget must not be negative")
    entries = tuple(ranked)
    by_key = {canonical_path_key(entry.path): entry for entry in snapshot.entries}
    anchor = _select_anchor(entries, by_key, query, touched_files)
    l0_candidates = (anchor,) if anchor is not None else ()
    l1_candidates, l2_candidates = _expand(snapshot.entries, l0_candidates)
    return _pack(
        snapshot.generation,
        l0_candidates[:2],
        l1_candidates[:8],
        l2_candidates[:16],
        token_budget,
    )


def _select_anchor(
    ranked: Sequence[RepoEntry],
    by_key: dict[str, RepoEntry],
    query: str,
    touched_files: Sequence[str],
) -> TierNode | None:
    normalized_query = query.replace("\\", "/")
    for match in _PATH_LINE.finditer(normalized_query):
        raw_path = match.group("path").strip()
        exact = by_key.get(canonical_path_key(raw_path)) if not _looks_absolute(raw_path) else None
        suffix_matches = tuple(
            candidate
            for candidate in by_key.values()
            if raw_path.casefold().endswith(candidate.path.casefold())
        )
        entry = exact or (suffix_matches[0] if len(suffix_matches) == 1 else None)
        if entry is not None:
            line = int(match.group("line"))
            symbol = next(
                (
                    item
                    for item in entry.symbols
                    if item.end_line is not None and item.line <= line <= item.end_line
                ),
                None,
            )
            if symbol is not None:
                return _node(entry, symbol, "symbol", ("explicit path:line",), 0)
            return TierNode(
                entry.path,
                "line",
                max(1, line - 20),
                line + 20,
                signature=entry.signature,
                reasons=("module-level path:line",),
            )
    query_folded = query.casefold()
    touched = {canonical_path_key(path) for path in touched_files}
    ranks = {entry.path: rank for rank, entry in enumerate(ranked)}
    candidates = sorted(
        ranked,
        key=lambda entry: (
            0 if canonical_path_key(entry.path) in touched else 1,
            0 if entry.path.casefold() in query_folded else 1,
            0 if any(symbol.name.casefold() in query_folded for symbol in entry.symbols) else 1,
            ranks[entry.path],
            entry.path.casefold(),
        ),
    )
    for entry in candidates:
        if not entry.symbols:
            continue
        symbol = next(
            (item for item in entry.symbols if item.name.casefold() in query_folded),
            entry.symbols[0],
        )
        return _node(entry, symbol, "symbol", ("task seed",), 0)
    return None


def _looks_absolute(path: str) -> bool:
    return path.startswith("/") or (len(path) >= 2 and path[1] == ":")


def _expand(
    entries: Sequence[RepoEntry], l0: Sequence[TierNode]
) -> tuple[tuple[TierNode, ...], tuple[TierNode, ...]]:
    if not l0:
        return (), ()
    by_path = {entry.path: entry for entry in entries}
    seed_paths = {node.path for node in l0}
    direct: dict[str, list[str]] = {}
    resolution: dict[str, str] = {}
    related_symbol: dict[str, str] = {}
    for entry in entries:
        for edge in entry.relations:
            if edge.target_path in seed_paths and entry.path not in seed_paths:
                reason = (
                    f"test impact candidate via {edge.kind}"
                    if _is_test_path(entry.path)
                    else f"incoming {edge.kind}"
                )
                direct.setdefault(entry.path, []).append(reason)
                resolution[entry.path] = edge.resolution
                related_symbol[entry.path] = edge.source_symbol
            if entry.path in seed_paths and edge.target_path and edge.target_path not in seed_paths:
                direct.setdefault(edge.target_path, []).append(f"outgoing {edge.kind}")
                resolution[edge.target_path] = edge.resolution
                related_symbol[edge.target_path] = edge.target_symbol
    ordered_direct = sorted(
        direct,
        key=lambda path: (
            0 if resolution[path] == "exact" else 1,
            1 if _is_test_path(path) else 0,
            path.casefold(),
            path,
        ),
    )
    l1 = tuple(
        _entry_node(
            by_path[path],
            tuple(sorted(set(direct[path]))),
            resolution[path],
            1,
            related_symbol.get(path, ""),
        )
        for path in ordered_direct[:8]
        if path in by_path
    )
    included = seed_paths | {node.path for node in l1}
    l2: list[TierNode] = []
    for entry in sorted(entries, key=lambda item: (item.path.casefold(), item.path)):
        if entry.path in included:
            continue
        incoming = next(
            (edge for edge in entry.relations if edge.target_path in included),
            None,
        )
        outgoing = next(
            (
                edge
                for source in entries
                if source.path in included
                for edge in source.relations
                if edge.target_path == entry.path
            ),
            None,
        )
        relation = incoming or outgoing
        if relation is not None or any(dep in included for dep in entry.dependencies):
            reason = (
                "test impact candidate at distance 2"
                if _is_test_path(entry.path)
                else "distance 2 dependency"
            )
            symbol_name = (
                relation.source_symbol if incoming is not None
                else relation.target_symbol if relation is not None
                else ""
            )
            l2.append(_entry_node(
                entry,
                (reason,),
                relation.resolution if relation is not None else "heuristic",
                2,
                symbol_name,
            ))
        if len(l2) >= 16:
            break
    return l1, tuple(l2)


def _pack(
    generation: int,
    l0: Sequence[TierNode],
    l1: Sequence[TierNode],
    l2: Sequence[TierNode],
    budget: int,
) -> TierSelection:
    if budget == 0:
        return TierSelection(generation, TOKENIZER_VERSION)
    large = tuple(node for node in l0 if node.end_line - node.start_line + 1 > 400)
    l0 = tuple(node for node in l0 if node not in large)
    l1 = tuple((*large, *l1))
    quotas = [budget * 60 // 100, budget * 25 // 100]
    quotas.append(budget - quotas[0] - quotas[1])
    selected: list[list[TierNode]] = [[], [], []]
    deferred: list[tuple[int, TierNode]] = []
    unused = 0
    for tier, (nodes, quota) in enumerate(zip((l0, l1, l2), quotas)):
        remaining = quota
        for node in nodes:
            cost = estimate_tokens(_render_node(tier, node))
            if cost <= remaining:
                selected[tier].append(node)
                remaining -= cost
            else:
                deferred.append((tier, node))
        unused += remaining
    deferred.sort(
        key=lambda item: (
            item[0],
            0 if item[1].resolution == "exact" else 1,
            item[1].distance,
            item[1].path.casefold(),
            item[1].start_line,
            item[1].symbol,
        )
    )
    overflow: list[TierNode] = [part for node in large for part in _split_target(node)]
    for tier, node in deferred:
        cost = estimate_tokens(_render_node(tier, node))
        if cost <= unused:
            selected[tier].append(node)
            unused -= cost
        elif tier == 0:
            overflow.extend(_split_target(node))
    result = TierSelection(
        generation,
        TOKENIZER_VERSION,
        tuple(selected[0][:2]),
        tuple(selected[1][:8]),
        tuple(selected[2][:16]),
        tuple(overflow),
    )
    return _trim_to_budget(result, budget)


def render_tier_selection(
    selection: TierSelection,
    token_budget: int,
    sources: dict[tuple[str, int, int], str] | None = None,
) -> str:
    if token_budget <= 0:
        return ""
    rendered = _render(selection, sources)
    if not rendered:
        return _compact_fallback(selection, token_budget)
    if estimate_tokens(rendered) <= token_budget:
        return rendered
    if sources and selection.l0:
        downgraded = TierSelection(
            selection.generation,
            selection.tokenizer_version,
            (),
            tuple((*selection.l0, *selection.l1))[:8],
            selection.l2,
            tuple((*selection.overflow_targets, *selection.l0)),
        )
        compacted = _render(_trim_to_budget(downgraded, token_budget))
        if compacted and estimate_tokens(compacted) <= token_budget:
            return compacted
        return _compact_fallback(downgraded, token_budget)
    compacted = _render(_trim_to_budget(selection, token_budget))
    if compacted and estimate_tokens(compacted) <= token_budget:
        return compacted
    return _compact_fallback(selection, token_budget)


def _trim_to_budget(selection: TierSelection, budget: int) -> TierSelection:
    groups = [list(selection.l0), list(selection.l1), list(selection.l2)]
    while any(groups):
        candidate = TierSelection(
            selection.generation,
            selection.tokenizer_version,
            tuple(groups[0]),
            tuple(groups[1]),
            tuple(groups[2]),
            selection.overflow_targets,
        )
        if estimate_tokens(_render(candidate)) <= budget:
            return candidate
        removable = 2 if groups[2] else 1 if groups[1] else 0
        groups[removable].pop()
    return TierSelection(
        selection.generation,
        selection.tokenizer_version,
        overflow_targets=selection.overflow_targets,
    )


def _render(
    selection: TierSelection,
    sources: dict[tuple[str, int, int], str] | None = None,
) -> str:
    blocks = [
        "Repository context [UNTRUSTED_REPOSITORY_DATA]",
        json.dumps(
            {
                "generation": selection.generation,
                "tokenizer_version": selection.tokenizer_version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    ]
    for tier, nodes in enumerate((selection.l0, selection.l1, selection.l2)):
        for node in nodes:
            blocks.append(
                _render_node(
                    tier,
                    node,
                    None if sources is None else sources.get(
                        (node.path, node.start_line, node.end_line)
                    ),
                )
            )
    for node in selection.overflow_targets:
        if node.signature is None:
            continue
        blocks.append(json.dumps({
            "kind": "read_code_slice_target",
            "path": node.path,
            "start_line": node.start_line,
            "end_line": node.end_line,
            "symbol": node.symbol,
            "expected_size_bytes": node.signature.size_bytes,
            "expected_modified_ns": node.signature.modified_ns,
            "expected_device_id": node.signature.device_id,
            "expected_file_id": node.signature.file_id,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return "\n".join(blocks) if len(blocks) > 2 else ""


def _render_node(tier: int, node: TierNode, source: str | None = None) -> str:
    payload = {
        "tier": f"L{tier}",
        "path": node.path,
        "kind": node.kind,
        "range": [node.start_line, node.end_line],
        "symbol": node.symbol,
        "symbol_signature": node.skeleton if tier == 1 else "",
        "docstring": node.docstring if tier == 1 else "",
        "reasons": node.reasons,
        "resolution": node.resolution,
        "source": source if tier == 0 and source is not None else "",
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _node(entry: RepoEntry, symbol: Symbol, kind: str, reasons: tuple[str, ...], distance: int) -> TierNode:
    return TierNode(
        entry.path,
        kind,
        symbol.line,
        symbol.end_line or symbol.line,
        symbol.name,
        entry.signature,
        symbol.signature,
        symbol.docstring,
        reasons,
        "exact",
        distance,
    )


def _entry_node(
    entry: RepoEntry, reasons: tuple[str, ...], resolution: str, distance: int,
    symbol_name: str = "",
) -> TierNode:
    symbol = next(
        (item for item in entry.symbols if item.name == symbol_name),
        entry.symbols[0] if entry.symbols else None,
    )
    if symbol is None:
        return TierNode(entry.path, "file", 0, 0, signature=entry.signature, reasons=reasons, resolution=resolution, distance=distance)
    return TierNode(
        entry.path,
        "symbol",
        symbol.line,
        symbol.end_line or symbol.line,
        symbol.name,
        entry.signature,
        symbol.signature,
        symbol.docstring,
        reasons,
        resolution,
        distance,
    )


def _is_test_path(path: str) -> bool:
    normalized = f"/{path.casefold()}"
    name = normalized.rsplit("/", 1)[-1]
    return "/tests/" in normalized or name.startswith("test_")


def _compact_fallback(selection: TierSelection, budget: int) -> str:
    nodes = (*selection.l0, *selection.l1, *selection.l2, *selection.overflow_targets)
    if not nodes or budget <= 0:
        return ""
    node = nodes[0]
    payload = "UNTRUSTED_REPOSITORY_DATA\n" + json.dumps(
        [node.path, node.symbol, node.start_line, node.end_line, selection.generation],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return truncate_to_tokens(payload, budget)


def _split_target(node: TierNode) -> tuple[TierNode, ...]:
    parts: list[TierNode] = []
    start = node.start_line
    while start <= node.end_line:
        end = min(node.end_line, start + 399)
        parts.append(TierNode(
            node.path, "slice", start, end, node.symbol, node.signature,
            reasons=("split oversized symbol",), resolution=node.resolution,
            distance=node.distance,
        ))
        start = end + 1
    return tuple(parts)
