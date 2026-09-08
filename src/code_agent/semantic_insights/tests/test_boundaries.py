from __future__ import annotations

import unittest

from code_agent.context.models import RepoEntry, Symbol
from code_agent.context.repo_index import RepoIndexSnapshot
from code_agent.semantic_insights import InsightSection, SemanticInsightService


class SemanticBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SemanticInsightService()

    def test_directory_boundary_and_symbol_drilldown(self) -> None:
        snapshot = RepoIndexSnapshot(1, (
            RepoEntry("src/a.py", (Symbol("src/a.py", "Demo", "class", 2),)),
            RepoEntry("src-other/b.py"),
        ))
        overview = self.service.analyze(snapshot, "overview", ("src",))
        labels = [item.label for section in overview.sections for item in section.items]
        self.assertIn("src/a.py:2 · Demo", labels)
        self.assertNotIn("src-other/b.py", labels)
        dead = self.service.analyze(snapshot, "dead-code", ("src",))
        self.assertEqual([i.label for i in dead.sections[0].items], ["src/a.py"])

    def test_all_scope_arguments_validated_before_analysis(self) -> None:
        snapshot = RepoIndexSnapshot(1, tuple(RepoEntry(f"src/{n}.py") for n in range(50)))
        with self.assertRaisesRegex(ValueError, "not present"):
            self.service.analyze(snapshot, "risk", ("src", "missing.py"))
        with self.assertRaisesRegex(ValueError, "at most 256"):
            self.service.analyze(RepoIndexSnapshot(1, tuple(
                RepoEntry(f"src/{n}.py") for n in range(257)
            )), "risk", ("src",))

    def test_risk_considers_paths_after_display_limit(self) -> None:
        snapshot = RepoIndexSnapshot(1, tuple(RepoEntry(f"src/{n}.py") for n in range(40)))
        report = self.service.analyze(snapshot, "risk", ("src",), limit=1)
        self.assertIn("all 40 requested files", report.summary)
        self.assertEqual(report.sections[1].total, 40)
        self.assertEqual(len(report.sections[1].items), 1)

    def test_pagination_preserves_totals_and_full_scope_computation(self) -> None:
        snapshot = RepoIndexSnapshot(1, tuple(RepoEntry(f"src/{n}.py") for n in range(5)))
        first = self.service.analyze(snapshot, "overview", limit=2)
        second = self.service.analyze(snapshot, "overview", limit=2, offset=2)
        files = next(s for s in second.sections if s.title == "Files")
        self.assertEqual((files.total, files.offset), (5, 2))
        self.assertEqual(len(files.items), 2)
        self.assertEqual(first.generation, second.generation)
        beyond = self.service.analyze(snapshot, "overview", offset=99)
        self.assertTrue(all(not s.items for s in beyond.sections))

    def test_deep_test_dependency_is_included_and_seed_not_a_consumer(self) -> None:
        entries = [RepoEntry("root.py")]
        previous = "root.py"
        for n in range(9):
            path = f"level{n}.py"
            entries.append(RepoEntry(path, dependencies=(previous,)))
            previous = path
        entries.append(RepoEntry("tests/test_deep.py", dependencies=(previous,)))
        snapshot = RepoIndexSnapshot(1, tuple(entries))
        report = self.service.analyze(snapshot, "tests", ("root.py",))
        self.assertEqual(report.sections[0].items[0].label, "tests/test_deep.py")
        self.assertIn("distance 10", report.sections[0].items[0].detail)
        impact = self.service.analyze(snapshot, "impact", ("root.py",))
        self.assertNotIn("root.py", [i.label for i in impact.sections[1].items])

    def test_refactor_cycles_have_no_fabricated_linear_order(self) -> None:
        snapshot = RepoIndexSnapshot(1, (
            RepoEntry("a.py", dependencies=("b.py",)),
            RepoEntry("b.py", dependencies=("a.py",)),
            RepoEntry("c.py", dependencies=("a.py",)),
        ))
        report = self.service.analyze(snapshot, "refactor", ("b.py",), limit=1)
        self.assertFalse(report.sections[0].items)
        self.assertIn("no safe linear order", report.sections[1].title)
        self.assertEqual(report.sections[1].total, 3)
        self.assertEqual(report.sections[-1].items[0].label, "b.py")
        impact = self.service.analyze(snapshot, "impact", ("b.py",))
        self.assertNotIn("b.py", [i.label for i in impact.sections[1].items])

    def test_review_explicit_target_comes_before_alphabetic_consumers(self) -> None:
        snapshot = RepoIndexSnapshot(1, (
            RepoEntry("z.py"), RepoEntry("a.py", dependencies=("z.py",)),
        ))
        report = self.service.analyze(snapshot, "review", ("z.py",), limit=1)
        self.assertEqual(report.sections[0].items[0].label, "z.py")
        self.assertEqual(report.sections[0].total, 2)

    def test_test_helpers_and_entrypoints_are_not_dead_code_candidates(self) -> None:
        path = "tests/test_demo.py"
        snapshot = RepoIndexSnapshot(1, (
            RepoEntry(path, (Symbol(path, "_fixture", "function", 1),)),
            RepoEntry("__main__.py"), RepoEntry("cli.py"),
        ))
        report = self.service.analyze(snapshot, "dead-code")
        self.assertFalse(report.sections[0].items)

    def test_empty_repo_overview_is_valid_but_risk_is_not(self) -> None:
        snapshot = RepoIndexSnapshot(0)
        self.assertIn("0 files", self.service.analyze(snapshot, "overview").sections[0].items[0].detail)
        with self.assertRaisesRegex(ValueError, "no indexed files"):
            self.service.analyze(snapshot, "risk", (".",))

    def test_invalid_inputs_fail_with_explicit_errors(self) -> None:
        snapshot = RepoIndexSnapshot(1, (RepoEntry("a.py"),))
        cases = (("risk", ("../a.py",), {}), ("locate", (" ",), {}),
                 ("context", ("x" * 4097,), {}), ("overview", (), {"offset": -1}),
                 ("overview", (), {"limit": True}), ("overview", ("x", "y"), {}))
        for kind, arguments, options in cases:
            with self.subTest(kind=kind, options=options), self.assertRaises(ValueError):
                self.service.analyze(snapshot, kind, arguments, **options)
        with self.assertRaises(ValueError):
            InsightSection("invalid", (), total=-1)


if __name__ == "__main__":
    unittest.main()
