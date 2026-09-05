from __future__ import annotations

from dataclasses import replace
from collections.abc import Callable, Sequence

from code_agent.context.repo_index import RepoIndexSnapshot
from code_agent.context.repo_semantic_graph import UnifiedSemanticGraph

from .inputs import (
    arguments as _arguments,
    lexical_paths as _lexical_paths,
    required_query as _required_query,
    resolve_paths as _resolve_paths,
    scope_paths,
)
from .models import InsightItem, InsightKind, InsightSection, SemanticInsightReport
from .ranking import (
    dead_code_candidates,
    rank_bug_locations,
    rank_context_nodes,
    rank_impacted_tests,
    reverse_distances,
)
from .projections import repository_sections, refactor_sections


_STATIC_LIMIT = (
    "Static graph coverage excludes unresolved dynamic dispatch, reflection, runtime "
    "registration, generated code, and external callers."
)


class SemanticInsightService:
    """Project one immutable semantic snapshot into bounded user reports."""

    def analyze(
        self,
        snapshot: RepoIndexSnapshot,
        kind: InsightKind | str,
        arguments: Sequence[str] = (),
        *,
        limit: int = 12,
        offset: int = 0,
        lexical_paths: Sequence[str] = (),
    ) -> SemanticInsightReport:
        if not isinstance(snapshot, RepoIndexSnapshot):
            raise TypeError("snapshot must be a RepoIndexSnapshot")
        resolved = kind if isinstance(kind, InsightKind) else InsightKind(kind)
        checked = _arguments(arguments)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100000:
            raise ValueError("offset must be between 0 and 100000")
        lexical = _lexical_paths(lexical_paths)
        if resolved in {InsightKind.CONTEXT, InsightKind.LOCATE}:
            report = self._lexical(snapshot, resolved, checked, len(snapshot.entries), lexical)
            return _bound_report(report, limit, offset)
        handlers: dict[InsightKind, Callable[..., SemanticInsightReport]] = {
            InsightKind.OVERVIEW: self._overview,
            InsightKind.IMPACT: self._impact,
            InsightKind.TESTS: self._tests,
            InsightKind.RISK: self._risk,
            InsightKind.REVIEW: self._review,
            InsightKind.REFACTOR: self._refactor,
            InsightKind.DEAD_CODE: self._dead_code,
        }
        report = handlers[resolved](snapshot, checked, len(snapshot.entries))
        return _bound_report(report, limit, offset)

    def _overview(
        self, snapshot: RepoIndexSnapshot, arguments: tuple[str, ...], limit: int
    ) -> SemanticInsightReport:
        sections = repository_sections(snapshot.semantic_graph, arguments)
        return _report(snapshot, InsightKind.OVERVIEW, "Repository Map", sections)

    def _impact(
        self, snapshot: RepoIndexSnapshot, arguments: tuple[str, ...], limit: int
    ) -> SemanticInsightReport:
        graph, changed = self._graph_paths(snapshot, arguments, limit)
        downstream = tuple(
            path for path in reverse_distances(graph, changed)
            if path not in changed and not _is_test(path)
        )
        tests = rank_impacted_tests(graph, changed, limit)
        sections = (
            _path_section("Changed files", changed, graph),
            _path_section("Affected code", downstream, graph),
            InsightSection("Affected tests", tests),
        )
        summary = f"{len(downstream)} code consumers · {len(tests)} impacted tests"
        return _report(snapshot, InsightKind.IMPACT, "Change Impact", sections, summary)

    def _tests(
        self, snapshot: RepoIndexSnapshot, arguments: tuple[str, ...], limit: int
    ) -> SemanticInsightReport:
        graph, changed = self._graph_paths(snapshot, arguments, limit)
        tests = rank_impacted_tests(graph, changed, limit)
        sections = (InsightSection("Priority order", tests),)
        summary = f"{len(tests)} statically impacted tests for {len(changed)} file(s)"
        return _report(snapshot, InsightKind.TESTS, "Test Priority", sections, summary)

    def _risk(
        self, snapshot: RepoIndexSnapshot, arguments: tuple[str, ...], limit: int
    ) -> SemanticInsightReport:
        graph, changed = self._graph_paths(snapshot, arguments, limit)
        tier, reason = graph.evaluate_risk(changed)
        items = tuple(
            InsightItem(
                path,
                f"{len(graph.get_dependents(path))} direct consumers · "
                f"{len(graph.find_impacted_tests((path,), max_depth=len(graph.nodes) + 1))} impacted tests",
                len(graph.get_dependents(path)),
            )
            for path in changed
        )
        sections = (
            InsightSection("Risk decision", (InsightItem(tier.upper(), reason),)),
            InsightSection("Risk drivers", items),
        )
        summary = f"Risk assessed across all {len(changed)} requested files; not a probability of failure"
        return _report(snapshot, InsightKind.RISK, "Pre-change Risk", sections, summary)

    def _review(
        self, snapshot: RepoIndexSnapshot, arguments: tuple[str, ...], limit: int
    ) -> SemanticInsightReport:
        graph, changed = self._graph_paths(snapshot, arguments, limit)
        consumers = {p for f in changed for p in graph.get_dependents(f)}
        tests = graph.find_impacted_tests(changed, max_depth=len(graph.nodes) + 1)
        scope = (*changed, *sorted((consumers | set(tests)).difference(changed)))
        items = tuple(
            InsightItem(path, _scope_reason(path, changed), confidence="static")
            for path in scope
        )
        sections = (InsightSection("Review files", items),)
        summary = f"{len(scope)} files in the bounded static review scope"
        return _report(snapshot, InsightKind.REVIEW, "Review Scope", sections, summary)

    def _refactor(
        self, snapshot: RepoIndexSnapshot, arguments: tuple[str, ...], limit: int
    ) -> SemanticInsightReport:
        graph, changed = self._graph_paths(snapshot, arguments, limit)
        sections = refactor_sections(graph, changed)
        return _report(snapshot, InsightKind.REFACTOR, "Refactor Impact", sections)

    def _lexical(
        self,
        snapshot: RepoIndexSnapshot,
        kind: InsightKind,
        arguments: tuple[str, ...],
        limit: int,
        lexical_paths: tuple[str, ...],
    ) -> SemanticInsightReport:
        label = "context query" if kind is InsightKind.CONTEXT else "bug description or symbol"
        query = _required_query(arguments, label)
        ranker = rank_context_nodes if kind is InsightKind.CONTEXT else rank_bug_locations
        items = ranker(snapshot.semantic_graph, query, limit, lexical_paths)
        section = "Recommended context" if kind is InsightKind.CONTEXT else "Investigation candidates"
        title = "Context Selection" if kind is InsightKind.CONTEXT else "Bug Localization"
        summary = f"{len(items)} graph-ranked candidates for: {query}"
        return _report(snapshot, kind, title, (InsightSection(section, items),), summary)

    def _dead_code(
        self, snapshot: RepoIndexSnapshot, arguments: tuple[str, ...], limit: int
    ) -> SemanticInsightReport:
        graph = snapshot.semantic_graph
        scope_paths(graph, arguments)
        prefix = arguments[0].replace("\\", "/").removeprefix("./").rstrip("/") if arguments else ""
        candidate_limit = len(graph.nodes) + sum(len(s) for s in graph.symbols.values())
        items = dead_code_candidates(graph, "" if prefix == "." else prefix, candidate_limit)
        sections = (InsightSection("Static candidates", items),)
        summary = f"{len(items)} candidates; manual confirmation is required before removal"
        return _report(snapshot, InsightKind.DEAD_CODE, "Dead Code Analysis", sections, summary)

    @staticmethod
    def _graph_paths(
        snapshot: RepoIndexSnapshot, arguments: tuple[str, ...], limit: int
    ) -> tuple[UnifiedSemanticGraph, tuple[str, ...]]:
        return snapshot.semantic_graph, _resolve_paths(snapshot.semantic_graph, arguments)


def _report(
    snapshot: RepoIndexSnapshot,
    kind: InsightKind,
    title: str,
    sections: tuple[InsightSection, ...],
    summary: str = "",
) -> SemanticInsightReport:
    return SemanticInsightReport(
        kind,
        snapshot.generation,
        title,
        summary,
        sections,
        (_STATIC_LIMIT, "Scope is limited to indexed files and resolved facts, not proof of complete repository coverage. "
         "Reports are advisory; they do not run tests, authorize edits, or replace verification evidence.",
         "Resolved dependency/call analysis currently covers Python; other languages have declaration/inventory support only. "
         "Scan limits, unreadable files, and parse errors may omit facts."),
    )


def _path_section(
    title: str, paths: Sequence[str], graph: UnifiedSemanticGraph
) -> InsightSection:
    return InsightSection(
        title,
        tuple(
            InsightItem(
                path,
                f"{len(graph.get_dependencies(path))} deps · "
                f"{len(graph.get_dependents(path))} consumers",
            )
            for path in paths
        ),
    )


def _scope_reason(path: str, changed: Sequence[str]) -> str:
    if path in changed:
        return "changed file"
    return "impacted test" if _is_test(path) else "direct consumer"

def _is_test(path: str) -> bool:
    parts = path.casefold().split("/")
    name = parts[-1]
    return "tests" in parts or name.startswith("test_") or name.endswith("_test.py")


def _bound_report(report: SemanticInsightReport, limit: int, offset: int) -> SemanticInsightReport:
    sections = tuple(replace(section, items=section.items[min(offset, section.total):offset + limit],
                             offset=min(offset, section.total)) for section in report.sections)
    return replace(report, sections=sections)
