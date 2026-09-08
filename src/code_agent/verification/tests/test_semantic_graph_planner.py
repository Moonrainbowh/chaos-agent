from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.models import RepoRelation, Symbol
from code_agent.context.repo_semantic_graph import UnifiedSemanticGraph
from code_agent.verification.planner import (
    RiskTier,
    VerificationPhase,
    VerificationPlan,
    VerificationPlanner,
)


class UnifiedSemanticGraphAndPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        # Build a synthetic semantic dependency graph
        # Topology:
        #   core_models.py (leaf dependency, heavily depended on)
        #   user_service.py -> depends on core_models.py
        #   order_service.py -> depends on core_models.py
        #   api_routes.py -> depends on user_service.py and order_service.py
        #   tests/test_user_service.py -> depends on user_service.py
        #   tests/test_api_routes.py -> depends on api_routes.py
        #   tests/test_tui.py -> depends on tui/view.py (independent)
        self.nodes = [
            "core_models.py",
            "user_service.py",
            "order_service.py",
            "api_routes.py",
            "tui/view.py",
            "tests/test_user_service.py",
            "tests/test_api_routes.py",
            "tests/test_tui.py",
        ]
        self.dependencies = {
            "core_models.py": (),
            "user_service.py": ("core_models.py",),
            "order_service.py": ("core_models.py",),
            "api_routes.py": ("user_service.py", "order_service.py"),
            "tui/view.py": (),
            "tests/test_user_service.py": ("user_service.py",),
            "tests/test_api_routes.py": ("api_routes.py",),
            "tests/test_tui.py": ("tui/view.py",),
        }
        self.relations = {
            "api_routes.py": (
                RepoRelation("call", "handle_user", 10, "user_service.py", "get_user", 5, 10),
            ),
            "tests/test_user_service.py": (
                RepoRelation("call", "test_get_user", 8, "user_service.py", "get_user", 5, 10),
            ),
        }
        self.graph = UnifiedSemanticGraph(
            nodes=self.nodes,
            dependencies=self.dependencies,
            relations=self.relations,
        )
        self.planner = VerificationPlanner(SRC_ROOT.parent, semantic_graph=self.graph)

    def test_reverse_dependents_computation(self) -> None:
        # core_models.py is imported by user_service and order_service
        dependents = self.graph.get_dependents("core_models.py")
        self.assertIn("user_service.py", dependents)
        self.assertIn("order_service.py", dependents)

        # Transitive dependents of core_models.py
        transitive = self.graph.get_transitive_dependents(["core_models.py"])
        self.assertIn("user_service.py", transitive)
        self.assertIn("order_service.py", transitive)
        self.assertIn("api_routes.py", transitive)
        self.assertIn("tests/test_user_service.py", transitive)
        self.assertIn("tests/test_api_routes.py", transitive)
        self.assertNotIn("tests/test_tui.py", transitive)

    def test_impacted_tests_isolates_unrelated_tests(self) -> None:
        # When user_service.py is changed:
        impacted = self.graph.find_impacted_tests(["user_service.py"])
        # Should include tests/test_user_service.py and downstream test_api_routes.py
        self.assertIn("tests/test_user_service.py", impacted)
        self.assertIn("tests/test_api_routes.py", impacted)
        # Must exclude test_tui.py
        self.assertNotIn("tests/test_tui.py", impacted)

    def test_change_risk_centrality_escalation(self) -> None:
        # Leaf node with no fan-in
        tier, _ = self.graph.evaluate_risk(["tui/view.py"])
        self.assertEqual(tier, RiskTier.MEDIUM.value)

        # Synthetic graph with high fan-in (8+ dependents)
        high_fan_in_deps = {f"consumer_{i}.py": ("shared_base.py",) for i in range(10)}
        high_fan_in_deps["shared_base.py"] = ()
        large_graph = UnifiedSemanticGraph(
            nodes=["shared_base.py", *high_fan_in_deps.keys()],
            dependencies=high_fan_in_deps,
            relations={},
        )
        tier, reason = large_graph.evaluate_risk(["shared_base.py"])
        self.assertEqual(tier, RiskTier.HIGH.value)
        self.assertIn("High fan-in centrality", reason)

    def test_review_scope_calculation(self) -> None:
        scope = self.graph.get_review_scope(["user_service.py"])
        self.assertIn("user_service.py", scope)
        # Direct consumers
        self.assertIn("api_routes.py", scope)
        # Impacted tests
        self.assertIn("tests/test_user_service.py", scope)
        # Unrelated module should not be in review scope
        self.assertNotIn("tui/view.py", scope)

    def test_refactor_order_topological_sort(self) -> None:
        # Given a group of interdependent files to refactor
        targets = ["api_routes.py", "core_models.py", "user_service.py"]
        ordered = self.graph.plan_refactor_order(targets)
        # core_models must come before user_service, which must come before api_routes
        self.assertEqual(ordered, ("core_models.py", "user_service.py", "api_routes.py"))

    def test_symbol_callers_lookup(self) -> None:
        callers = self.graph.find_symbol_callers("user_service.py", "get_user")
        self.assertEqual(len(callers), 2)
        sources = {c.target_path for c in callers}
        self.assertEqual(sources, {"user_service.py"})

    def test_planner_delegation_to_semantic_graph(self) -> None:
        # Phase 2 Local Milestone with semantic graph
        plan = self.planner.plan(["user_service.py"], phase=VerificationPhase.LOCAL_MILESTONE)
        self.assertEqual(plan.phase, VerificationPhase.LOCAL_MILESTONE)
        self.assertIn("tests/test_user_service.py", plan.targeted_tests)
        self.assertNotIn("tests/test_tui.py", plan.targeted_tests)
        self.assertFalse(plan.require_full_gate)

        # Review scope and refactor order exposed via planner
        scope = self.planner.get_review_scope(["user_service.py"])
        self.assertIn("api_routes.py", scope)

        refactor = self.planner.plan_refactor_order(["api_routes.py", "core_models.py"])
        self.assertEqual(refactor, ("core_models.py", "api_routes.py"))


if __name__ == "__main__":
    unittest.main()
