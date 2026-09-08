from __future__ import annotations

import unittest

from code_agent.context.models import RepoEntry, RepoRelation, Symbol
from code_agent.context.repo_index import RepoIndexSnapshot
from code_agent.semantic_insights import InsightKind, SemanticInsightService


def _entry(
    path: str,
    *,
    dependencies: tuple[str, ...] = (),
    symbols: tuple[Symbol, ...] = (),
) -> RepoEntry:
    relations = tuple(
        RepoRelation("import", target_path=target, resolution="exact")
        for target in dependencies
    )
    return RepoEntry(path, symbols, dependencies, relations=relations)


class SemanticInsightServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        core = "src/pkg/core.py"
        service = "src/pkg/service.py"
        self.snapshot = RepoIndexSnapshot(
            7,
            (
                _entry(
                    core,
                    symbols=(Symbol(core, "CoreAPI", "class", 3, 12),),
                ),
                _entry(
                    service,
                    dependencies=(core,),
                    symbols=(Symbol(service, "run_service", "function", 4, 9),),
                ),
                _entry("src/pkg/unused.py"),
                _entry("tests/test_core.py", dependencies=(core,)),
                _entry("tests/test_service.py", dependencies=(service,)),
            ),
        )
        self.service = SemanticInsightService()

    def test_overview_exposes_generation_inventory_and_hubs(self) -> None:
        report = self.service.analyze(self.snapshot, "overview")

        self.assertEqual(report.generation, 7)
        self.assertEqual(report.kind, InsightKind.OVERVIEW)
        self.assertIn("5 files", report.sections[0].items[0].detail)
        self.assertIn("src/pkg/core.py", _labels(report))

    def test_context_and_bug_location_rank_matching_symbol_and_neighbors(self) -> None:
        context = self.service.analyze(self.snapshot, "context", ("CoreAPI",))
        locate = self.service.analyze(self.snapshot, "locate", ("service",))

        self.assertEqual(context.sections[0].items[0].label, "src/pkg/core.py")
        self.assertIn("src/pkg/service.py", _labels(locate))
        self.assertTrue(all(item.confidence == "heuristic" for item in locate.sections[0].items))

    def test_lexical_source_hits_seed_graph_context_and_bug_ranking(self) -> None:
        context = self.service.analyze(
            self.snapshot,
            "context",
            ("opaque business phrase",),
            lexical_paths=("src/pkg/core.py",),
        )

        self.assertEqual(context.sections[0].items[0].label, "src/pkg/core.py")
        self.assertIn("lexical source match", context.sections[0].items[0].detail)

    def test_impact_and_test_priority_follow_reverse_dependencies(self) -> None:
        impact = self.service.analyze(
            self.snapshot, "impact", ("src/pkg/core.py",)
        )
        tests = self.service.analyze(
            self.snapshot, "tests", ("src/pkg/core.py",)
        )

        self.assertIn("src/pkg/service.py", _labels(impact))
        self.assertEqual(
            tuple(item.label for item in tests.sections[0].items),
            ("tests/test_core.py", "tests/test_service.py"),
        )
        self.assertGreater(
            tests.sections[0].items[0].score,
            tests.sections[0].items[1].score,
        )

    def test_risk_review_and_refactor_share_the_same_targets(self) -> None:
        arguments = ("src/pkg/core.py",)
        risk = self.service.analyze(self.snapshot, "risk", arguments)
        review = self.service.analyze(self.snapshot, "review", arguments)
        refactor = self.service.analyze(self.snapshot, "refactor", arguments)

        self.assertIn(risk.sections[0].items[0].label, {"MEDIUM", "HIGH"})
        self.assertIn("tests/test_core.py", _labels(review))
        order = tuple(item.label for item in refactor.sections[0].items)
        self.assertLess(order.index("src/pkg/core.py"), order.index("src/pkg/service.py"))

    def test_dead_code_reports_candidates_without_claiming_safe_removal(self) -> None:
        report = self.service.analyze(self.snapshot, "dead-code", ("src/pkg",))

        self.assertIn("src/pkg/unused.py", _labels(report))
        self.assertIn("manual confirmation", report.summary)
        self.assertEqual(
            next(item for item in report.sections[0].items if item.label == "src/pkg/unused.py").confidence,
            "candidate",
        )
        self.assertTrue(report.warnings)

    def test_unknown_path_and_missing_query_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "not present"):
            self.service.analyze(self.snapshot, "risk", ("missing.py",))
        with self.assertRaisesRegex(ValueError, "required"):
            self.service.analyze(self.snapshot, "locate")


def _labels(report: object) -> set[str]:
    return {
        item.label
        for section in report.sections
        for item in section.items
    }


if __name__ == "__main__":
    unittest.main()
