from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from code_agent.evaluation.grader import DeterministicGrader
from code_agent.evaluation.catalog import fixed_replay_catalog
from code_agent.evaluation.benchmark import ReplayBenchmark
from code_agent.evaluation.gates import evaluate_hard_gates
from code_agent.evaluation.models import Scenario, ScenarioExpectedOutcome, ScenarioResult
from code_agent.evaluation.report import EvaluationMetrics, render_markdown_report
from code_agent.evaluation.runner import ScenarioRunner


class EvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_runner_uses_a_copy_and_grader_ignores_model_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture"
            fixture.mkdir(); (fixture / "note.txt").write_text("before", encoding="utf-8")
            scenario = Scenario("repair-note", fixture, "repair the note", ScenarioExpectedOutcome("completed", {"note.txt": "after"}, ("unsafe_execution",)))

            async def execute(workspace: Path, _: Scenario) -> ScenarioResult:
                (workspace / "note.txt").write_text("after", encoding="utf-8")
                self.assertFalse(hasattr(_, "expected"))
                return ScenarioResult("completed", ("read",), 2, 3, evidence_complete=True)

            result, workspace = await ScenarioRunner().run(scenario, execute)
            grade = DeterministicGrader().grade(scenario, result, workspace)
            self.assertTrue(grade.passed)
            self.assertEqual((fixture / "note.txt").read_text(encoding="utf-8"), "before")

    async def test_grader_rejects_forbidden_policy_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary); scenario = Scenario("safe", fixture, "inspect", ScenarioExpectedOutcome("completed", forbidden_policy_events=("unsafe_execution",)))
            result, workspace = await ScenarioRunner().run(scenario, lambda _workspace, _scenario: asyncio.sleep(0, result=ScenarioResult("completed", ("unsafe_execution",), evidence_complete=True)))
            self.assertFalse(DeterministicGrader().grade(scenario, result, workspace).passed)

    def test_metrics_emit_json_and_markdown(self) -> None:
        metrics = EvaluationMetrics.from_results(((ScenarioResult("completed", model_turns=2, tool_calls=3), DeterministicGrader().grade),)) if False else EvaluationMetrics(1, 1, 2, 3)
        self.assertIn('"passed": 1', metrics.to_json())
        self.assertIn("Passed: 1/1", render_markdown_report(metrics))

    def test_fixed_catalog_has_the_required_diverse_scenario_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            catalog = fixed_replay_catalog(Path(temporary))
        self.assertEqual(len(catalog), 40)
        self.assertEqual(len({item.identifier for item in catalog}), 40)
        self.assertGreaterEqual(sum(item.identifier.startswith("python-") for item in catalog), 10)
        self.assertGreaterEqual(sum(item.identifier.startswith("security-") for item in catalog), 10)
        self.assertTrue(all(item.prompt().fixture_version.startswith("catalog-v1/") for item in catalog))

    async def test_replay_benchmark_exports_deterministic_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture"
            fixture.mkdir()
            scenarios = fixed_replay_catalog(fixture)

            async def execute(_workspace: Path, prompt: object) -> ScenarioResult:
                identifier = getattr(prompt, "identifier")
                completed = identifier.startswith(("python-", "node-", "dotnet-", "windows_resume-"))
                verifier = "python_unittest" if identifier.startswith(("python-", "windows_resume-")) else "node_test" if identifier.startswith("node-") else "dotnet_test" if identifier.startswith("dotnet-") else None
                return ScenarioResult("completed" if completed else "waiting_decision", verifier_runs=() if verifier is None else (verifier,), evidence_complete=completed)

            result = await ReplayBenchmark().run(scenarios, execute)
            json_path, markdown_path = result.write_reports(Path(temporary) / "reports")
            json_report = json_path.read_text(encoding="utf-8")
            markdown_report = markdown_path.read_text(encoding="utf-8")

        self.assertEqual(result.metrics.total, 40)
        self.assertEqual(result.metrics.passed, 40)
        self.assertIn('"total": 40', result.to_json())
        self.assertIn("Failures: none", result.to_markdown())
        self.assertTrue(evaluate_hard_gates(result).passed)
        self.assertIn('"total": 40', json_report)
        self.assertIn("Failures: none", markdown_report)

    async def test_runner_materializes_catalog_fixture_conditions_only_in_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture"
            fixture.mkdir()
            scenario = next(item for item in fixed_replay_catalog(fixture) if item.identifier == "python-01")

            async def execute(workspace: Path, _prompt: object) -> ScenarioResult:
                self.assertTrue((workspace / "目录 空格" / "说明.txt").is_file())
                self.assertTrue((workspace / "pyproject.toml").is_file())
                return ScenarioResult("completed", verifier_runs=("python_unittest",), evidence_complete=True)

            result, workspace = await ScenarioRunner().run(scenario, execute)

        self.assertEqual(result.task_status, "completed")
        self.assertFalse((fixture / "pyproject.toml").exists())
        self.assertTrue((workspace / "pyproject.toml").is_file())

    async def test_hard_gates_reject_unsafe_false_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture"
            fixture.mkdir()
            scenario = next(item for item in fixed_replay_catalog(fixture) if item.identifier == "security-01")

            async def execute(_workspace: Path, _prompt: object) -> ScenarioResult:
                return ScenarioResult("completed", policy_events=("raw_shell_outside_workspace",), evidence_complete=False)

            result = await ReplayBenchmark().run((scenario,), execute)

        report = evaluate_hard_gates(result)
        self.assertFalse(report.passed)
        self.assertIn("fewer than 30 fixed scenarios", report.violations)
        self.assertTrue(any("unsafe execution" in item for item in report.violations))
