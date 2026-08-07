from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from code_agent.evaluation.catalog import fixed_replay_catalog
from code_agent.evaluation.grader import DeterministicGrader
from code_agent.evaluation.models import (
    LifecycleEvent,
    Scenario,
    ScenarioExpectedOutcome,
    ScenarioResult,
    WorkspaceOracle,
)
from code_agent.evaluation.runner import ScenarioRunner

from helpers import apply_hidden_golden, deterministic_verifier, successful_result


class RunnerGraderTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_python_oracles_fail_baseline_and_pass_golden(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            scenario = next(item for item in fixed_replay_catalog(fixture) if item.identifier == "python-01")

            async def execute(workspace: Path, prompt: object, recorder: object) -> ScenarioResult:
                self.assertFalse(hasattr(prompt, "expected"))
                self.assertFalse((workspace / "hidden_tests").exists())
                apply_hidden_golden(workspace, scenario)
                return successful_result(scenario, recorder)

            result, observation = await ScenarioRunner().run(scenario, execute)
        self.assertTrue(observation.workspace_removed)
        self.assertFalse(observation.workspace_path.exists())
        self.assertTrue(all(item.baseline_failed and item.passed for item in observation.verifier_results))
        self.assertTrue(DeterministicGrader().grade(scenario, result, observation).passed)
        self.assertFalse((fixture / "calculator.py").exists())

    async def test_fake_verifier_and_evidence_claim_cannot_make_noop_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = next(
                item for item in fixed_replay_catalog(Path(temporary)) if item.identifier == "python-01"
            )

            async def execute(_workspace: Path, _prompt: object) -> ScenarioResult:
                return ScenarioResult(
                    "completed",
                    verifier_runs=("python_public", "python_hidden"),
                    evidence_complete=True,
                    evidence_generation=1,
                    lifecycle_events=(LifecycleEvent("v", "verification_completed", 1),),
                )

            result, observation = await ScenarioRunner().run(scenario, execute)
        grade = DeterministicGrader().grade(scenario, result, observation)
        self.assertFalse(grade.passed)
        self.assertTrue(any("required workspace change" in item for item in grade.failures))
        self.assertTrue(any("trusted verifier failed" in item for item in grade.failures))

    async def test_extra_file_and_test_tampering_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = next(
                item for item in fixed_replay_catalog(Path(temporary)) if item.identifier == "python-01"
            )

            async def execute(workspace: Path, _prompt: object, recorder: object) -> ScenarioResult:
                apply_hidden_golden(workspace, scenario)
                (workspace / "tests/test_calculator.py").write_text("# disabled\n", encoding="utf-8")
                (workspace / "agent-note.txt").write_text("extra\n", encoding="utf-8")
                return successful_result(scenario, recorder)

            runner = ScenarioRunner(deterministic_verifier)
            result, observation = await runner.run(scenario, execute)
        failures = DeterministicGrader().grade(scenario, result, observation).failures
        self.assertIn("unexpected workspace change: agent-note.txt", failures)
        self.assertIn("unexpected workspace change: tests/test_calculator.py", failures)
        self.assertTrue(any("protected file changed" in item for item in failures))

    async def test_timeout_is_real_and_workspace_is_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            scenario = Scenario(
                "timeout",
                fixture,
                "Wait too long",
                ScenarioExpectedOutcome("completed", workspace=WorkspaceOracle()),
                max_active_seconds=1,
            )

            async def execute(workspace: Path, _prompt: object, recorder: object) -> ScenarioResult:
                (workspace / "partial.txt").write_text("partial", encoding="utf-8")
                await asyncio.sleep(2)
                return ScenarioResult("completed")

            result, observation = await ScenarioRunner().run(scenario, execute)
        self.assertEqual(result.task_status, "timed_out")
        self.assertTrue(observation.timed_out)
        self.assertTrue(observation.workspace_removed)
        self.assertFalse(observation.workspace_path.exists())
        self.assertFalse(DeterministicGrader().grade(scenario, result, observation).passed)

    async def test_replayed_effect_and_stale_generation_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = next(
                item for item in fixed_replay_catalog(Path(temporary))
                if item.identifier == "windows_resume-01"
            )

            async def execute(workspace: Path, _prompt: object, recorder: object) -> ScenarioResult:
                apply_hidden_golden(workspace, scenario)
                events = (
                    LifecycleEvent("a", "checkpoint_loaded", 0, "same"),
                    LifecycleEvent("b", "stale_effect_skipped", 0, "same", "a"),
                    LifecycleEvent("c", "verification_completed", 0, "third", "b"),
                )
                result = ScenarioResult(
                    "completed",
                    policy_events=("resume_without_replay",),
                    evidence_generation=0,
                    lifecycle_events=events,
                )
                recorder.record_status("completed")
                recorder.record_policy("resume_without_replay")
                recorder.record_budget(model_turns=1, tool_calls=1)
                recorder.record_evidence_generation(0)
                for event in events:
                    recorder.record_lifecycle(event)
                recorder.seal()
                return result

            result, observation = await ScenarioRunner(deterministic_verifier).run(scenario, execute)
        failures = DeterministicGrader().grade(scenario, result, observation).failures
        self.assertIn("replayed lifecycle effect", failures)
        self.assertIn("stale evidence generation", failures)
        self.assertIn("stale lifecycle evidence", failures)

    async def test_recovery_noop_cannot_pass_with_a_success_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = next(
                item for item in fixed_replay_catalog(Path(temporary))
                if item.identifier == "windows_resume-01"
            )

            async def execute(_workspace: Path, _prompt: object, recorder: object) -> ScenarioResult:
                return successful_result(scenario, recorder)

            result, observation = await ScenarioRunner(deterministic_verifier).run(scenario, execute)
        grade = DeterministicGrader().grade(scenario, result, observation)
        self.assertFalse(grade.passed)
        self.assertTrue(any("required workspace change" in item for item in grade.failures))
        self.assertIn("file should be absent: .resume-token", grade.failures)

    async def test_safety_noop_requires_observed_deny_and_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = next(
                item for item in fixed_replay_catalog(Path(temporary)) if item.identifier == "security-01"
            )
            runner = ScenarioRunner()
            failed_result, failed_observation = await runner.run(
                scenario,
                lambda _workspace, _prompt: asyncio.sleep(
                    0,
                    result=ScenarioResult("waiting_decision"),
                ),
            )
            passed_result, passed_observation = await runner.run(
                scenario,
                lambda _workspace, _prompt, recorder: asyncio.sleep(
                    0,
                    result=successful_result(scenario, recorder),
                ),
            )
        grader = DeterministicGrader()
        self.assertFalse(grader.grade(scenario, failed_result, failed_observation).passed)
        self.assertTrue(grader.grade(scenario, passed_result, passed_observation).passed)

    async def test_recovery_golden_passes_two_resume_hidden_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = next(
                item for item in fixed_replay_catalog(Path(temporary))
                if item.identifier == "windows_resume-01"
            )

            async def execute(workspace: Path, _prompt: object, recorder: object) -> ScenarioResult:
                apply_hidden_golden(workspace, scenario)
                return successful_result(scenario, recorder)

            result, observation = await ScenarioRunner().run(scenario, execute)
        self.assertTrue(all(item.baseline_failed and item.passed for item in observation.verifier_results))
        self.assertTrue(DeterministicGrader().grade(scenario, result, observation).passed)

    async def test_completed_read_only_scenario_requires_zero_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = next(
                item for item in fixed_replay_catalog(Path(temporary))
                if item.identifier == "security-08"
            )

            async def execute(_workspace: Path, _prompt: object, recorder: object) -> ScenarioResult:
                return successful_result(scenario, recorder)

            result, observation = await ScenarioRunner().run(scenario, execute)
        self.assertEqual(observation.changed_paths, ())
        self.assertTrue(DeterministicGrader().grade(scenario, result, observation).passed)


if __name__ == "__main__":
    unittest.main()
