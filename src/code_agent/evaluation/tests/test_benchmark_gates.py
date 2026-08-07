from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from code_agent.evaluation.benchmark import ReplayBenchmark
from code_agent.evaluation.catalog import fixed_replay_catalog
from code_agent.evaluation.gates import evaluate_hard_gates
from code_agent.evaluation.grader import Grade
from code_agent.evaluation.report import EvaluationMetrics, render_markdown_report
from code_agent.evaluation.runner import ScenarioRunner

from helpers import apply_hidden_golden, deterministic_verifier, successful_result


class BenchmarkGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_realistic_corpus_passes_exact_hard_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = fixed_replay_catalog(root)
            by_id = {scenario.identifier: scenario for scenario in catalog}

            async def execute(workspace: Path, prompt: object, recorder: object):
                scenario = by_id[getattr(prompt, "identifier")]
                apply_hidden_golden(workspace, scenario)
                return successful_result(scenario, recorder)

            benchmark = ReplayBenchmark(runner=ScenarioRunner(deterministic_verifier))
            result = await benchmark.run(catalog, execute)
            json_path, markdown_path = result.write_reports(root / "reports")
            json_report = json_path.read_text(encoding="utf-8")
            markdown_report = markdown_path.read_text(encoding="utf-8")
        self.assertEqual(result.metrics.total, 40)
        self.assertEqual(result.metrics.passed, 40)
        self.assertTrue(evaluate_hard_gates(result).passed)
        self.assertIn('"total": 40', json_report)
        self.assertIn('"changed_paths"', json_report)
        self.assertIn('"final_digest"', json_report)
        self.assertIn('"infrastructure_failures"', json_report)
        self.assertIn('"workspace_removed"', json_report)
        self.assertIn("Failures: none", markdown_report)

    async def test_gates_reject_partial_duplicate_and_quota_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            catalog = fixed_replay_catalog(Path(temporary))
            scenario = catalog[-1]

            async def execute(workspace: Path, _prompt: object, recorder: object):
                apply_hidden_golden(workspace, scenario)
                return successful_result(scenario, recorder)

            partial = await ReplayBenchmark(runner=ScenarioRunner(deterministic_verifier)).run(
                (scenario,),
                execute,
            )
        violations = evaluate_hard_gates(partial).violations
        self.assertIn("fixed corpus must contain exactly 40 scenarios", violations)
        self.assertIn("fixed corpus category quotas changed", violations)

        repeated = replace(partial, records=partial.records * 40)
        self.assertIn(
            "fixed corpus scenario identifiers must be unique",
            evaluate_hard_gates(repeated).violations,
        )
        forged_grade = replace(partial.records[0], grade=Grade(False, ("forged",)))
        forged = replace(partial, records=(forged_grade,))
        self.assertTrue(
            any("stored grade is inconsistent" in item for item in evaluate_hard_gates(forged).violations)
        )
        tampered = replace(partial, corpus_fingerprint="0" * 64)
        self.assertIn(
            "reported corpus fingerprint is inconsistent",
            evaluate_hard_gates(tampered).violations,
        )

    def test_metrics_emit_deterministic_json_and_markdown(self) -> None:
        metrics = EvaluationMetrics(4, 3, 8, 12)
        self.assertIn('"passed": 3', metrics.to_json())
        self.assertIn("Passed: 3/4", render_markdown_report(metrics))


if __name__ == "__main__":
    unittest.main()
