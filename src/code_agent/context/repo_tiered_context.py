from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from collections.abc import Sequence

from .models import FileSignature, RepoEntry, RepoRelation, Symbol
from .repo_index import RepoIndexSnapshot
from .repo_paths import canonical_path_key
from .repo_query import contract_query_terms, expand_search_terms, plan_repo_query
from .repo_ranking import _DOCS_QUERY
from .repo_query import bound_repo_query
from .tokens import estimate_tokens, truncate_to_tokens


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
    candidates: tuple[tuple[str, TierNode], ...] = field(default=(), compare=False, repr=False)


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
    l0_candidates = _select_anchor(entries, by_key, query, touched_files)
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
) -> tuple[TierNode, ...]:
    """Select up to two non-overlapping anchors, strongest evidence first."""
    mentions, symbol_query = _anchor_mentions(by_key, query)
    touched = {canonical_path_key(path) for path in touched_files}
    entries = tuple(dict.fromkeys((*[entry for entry, _ in mentions], *ranked)))
    explicit = []
    for entry, line in mentions:
        if line is not None:
            containing = [s for s in entry.symbols if s.line <= line <= (s.end_line or s.line)]
            symbol = min(containing, key=lambda s: ((s.end_line or s.line) - s.line, s.line, s.name), default=None)
            explicit.append(_node(entry, symbol, "symbol", ("explicit path:line",), 0)
                            if symbol else _module_anchor(entry, line))
    # Exact identifiers can locate symbols even outside the file retrieval list.
    for entry in dict.fromkeys((*entries, *by_key.values())):
        for symbol in sorted(entry.symbols, key=lambda s: (s.line, s.name)):
            if re.search(r"(?<![\w])" + re.escape(symbol.name) + r"(?![\w])", symbol_query, re.I):
                explicit.append(_node(entry, symbol, "symbol", ("explicit symbol",), 0))
    selected = _independent_anchors(explicit)
    terms = set(contract_query_terms(plan_repo_query(symbol_query))) - _ANCHOR_STOP_TERMS
    scored = sorted(
        ((-_symbol_score(symbol, terms), 0 if canonical_path_key(entry.path) in touched else 1,
          rank, symbol.line, symbol.name, entry.path, entry, symbol)
         for rank, entry in enumerate(entries) for symbol in entry.symbols),
        key=lambda item: item[:6],
    )
    # An explicitly named file gets its own anchor before unrelated retrieved files.
    for entry, _ in mentions:
        if any(node.path == entry.path for node in selected):
            continue
        best = next((item for item in scored if item[6].path == entry.path and item[0] <= -4), None)
        node = (_node(entry, best[7], "symbol", ("file-to-symbol relevance",), 0)
                if best else _module_anchor(entry))
        selected = _independent_anchors((*selected, node))
    reranked = [_node(item[6], item[7], "symbol", ("file-to-symbol relevance",), 0)
                for item in scored if item[0] <= -4]
    if not selected:
        selected = _independent_anchors(reranked)
    if not selected and entries:
        entry = min(entries, key=lambda e: (
            0 if canonical_path_key(e.path) in touched else 1,
            0 if _DOCS_QUERY.search(bound_repo_query(query)) or e.symbols or e.path.endswith(".py") else 1,
        ))
        selected = (_module_anchor(entry),)
    return selected


_ANCHOR_STOP_TERMS = frozenset({
    "def", "class", "self", "return", "none", "true", "false", "py", "function",
    "file", "fix", "repair", "please", "the", "a", "an", "in", "of", "to", "and",
})


def _anchor_mentions(
    by_key: dict[str, RepoEntry], query: str,
) -> tuple[list[tuple[RepoEntry, int | None]], str]:
    normalized = query.replace("\\", "/")
    matches = []
    for entry in by_key.values():
        pattern = r"(?<![\w.-])" + re.escape(entry.path) + r"(?::([1-9][0-9]*))?(?![\w./-])"
        for match in re.finditer(pattern, normalized, re.I):
            matches.append((match.start(), match.end(), entry, int(match[1]) if match[1] else None))
    accepted = []
    for start, end, entry, line in sorted(matches, key=lambda item: (item[0], -item[1], item[2].path)):
        if accepted and start < accepted[-1][1]:
            continue
        accepted.append((start, end, entry, line))
    chars = list(normalized)
    for start, end, _, _ in accepted:
        chars[start:end] = " " * (end - start)
    return [(entry, line) for _, _, entry, line in accepted], "".join(chars)


def _symbol_score(symbol: Symbol, terms: set[str]) -> int:
    """One name term or two metadata terms reach the minimum score of four."""
    def words(text: str) -> set[str]:
        return set(re.findall(r"\w+", expand_search_terms(text)))

    return (4 * len(terms & words(symbol.name))
            + 2 * len(terms & words(symbol.signature))
            + 2 * len(terms & words(symbol.docstring)))


def _module_anchor(entry: RepoEntry, line: int | None = None) -> TierNode:
    return TierNode(
        entry.path, "line", max(1, line - 20) if line else 1,
        line + 20 if line else 40, signature=entry.signature,
        reasons=("module-level path:line" if line else "bounded module fallback",),
    )


def _independent_anchors(nodes: Sequence[TierNode]) -> tuple[TierNode, ...]:
    result: list[TierNode] = []
    for node in nodes:
        if any(node.path == other.path and node.start_line <= other.end_line
               and other.start_line <= node.end_line for other in result):
            continue
        result.append(node)
        if len(result) == 2:
            break
    return tuple(result)


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
    candidates = tuple((f"L{tier}", node) for tier, nodes in enumerate((l0, l1, l2)) for node in nodes)
    large = tuple(node for node in l0 if node.end_line - node.start_line + 1 > 400)
    l0 = tuple(node for node in l0 if node not in large)
    l1 = tuple((*large, *l1))
    # This cached selection is provisional: source is read and signature-checked
    # by RepoMapBuilder before render_tier_selection applies the real cost.
    result = TierSelection(
        generation,
        TOKENIZER_VERSION,
        tuple(l0[:2]),
        tuple(l1[:8]),
        tuple(l2[:16]),
        tuple(part for node in large for part in _split_target(node)),
    )
    return replace(_trim_to_budget(result, budget), candidates=candidates)


def render_tier_selection(
    selection: TierSelection,
    token_budget: int,
    sources: dict[tuple[str, int, int], str] | None = None,
) -> str:
    if token_budget <= 0:
        return ""
    compacted = _render(_trim_to_budget(selection, token_budget, sources), sources)
    if compacted and estimate_tokens(compacted) <= token_budget:
        return compacted
    return _compact_fallback(selection, token_budget)


def _trim_to_budget(
    selection: TierSelection,
    budget: int,
    sources: dict[tuple[str, int, int], str] | None = None,
) -> TierSelection:
    groups = [list(selection.l0), list(selection.l1), list(selection.l2)]
    overflow = list(selection.overflow_targets)
    while any(groups) or overflow:
        candidate = TierSelection(
            selection.generation,
            selection.tokenizer_version,
            tuple(groups[0]),
            tuple(groups[1]),
            tuple(groups[2]),
            tuple(overflow),
        )
        # Count the complete JSON payload, including escaped source and headers.
        if estimate_tokens(_render(candidate, sources)) <= budget:
            return candidate
        if groups[2]:
            groups[2].pop()
        elif groups[1]:
            groups[1].pop()
        elif overflow:
            overflow.pop()
        else:
            # Only L0 remains and still cannot fit. Preserve higher-priority
            # anchors, and expose bounded reads for the removed source.
            node = groups[0].pop()
            groups[1].append(node)
            overflow.extend(_split_target(node))
    # Keep a bounded-read identity for the compact fallback at tiny budgets.
    return TierSelection(
        selection.generation, selection.tokenizer_version,
        overflow_targets=selection.overflow_targets or tuple(
            part for node in selection.l0 for part in _split_target(node)
        ),
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
