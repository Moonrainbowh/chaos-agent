from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.evaluation.catalog import fixed_replay_catalog
from code_agent.evaluation.grader import DeterministicGrader
from code_agent.evaluation.models import (
    Scenario,
    ScenarioExpectedOutcome,
    ScenarioResult,
    VerifierOracle,
    WorkspaceOracle,
)
from code_agent.evaluation.process_executor import ProcessScenarioExecutor
from code_agent.evaluation.runner import ScenarioRunner
from code_agent.evaluation.verifier import CommandOutcome, SubprocessVerifier

from helpers import apply_hidden_golden, deterministic_verifier, successful_result


class IsolationAndTrustTests(unittest.IsolatedAsyncioTestCase):
    async def test_verifier_root_cwd_accepts_resolved_parent_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            actual_parent = Path(temporary) / "actual"
            actual_workspace = actual_parent / "workspace"
            actual_workspace.mkdir(parents=True)
            workspace = actual_workspace
            if os.name != "nt":
                alias_parent = Path(temporary) / "alias"
                alias_parent.symlink_to(actual_parent, target_is_directory=True)
                workspace = alias_parent / "workspace"
                self.assertNotEqual(workspace, workspace.resolve())
            expected_cwd = repr(str(actual_workspace.resolve()))
            oracle = VerifierOracle(
                "root-cwd",
                (
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; assert Path.cwd() == Path({expected_cwd})",
                ),
                baseline_must_fail=False,
            )
            outcome = await SubprocessVerifier()(workspace, oracle, "baseline")
        self.assertEqual(outcome, CommandOutcome(0))

    async def test_verifier_rejects_link_at_workspace_or_cwd_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            linked_cwd = workspace / "linked"
            linked_cwd.mkdir(parents=True)
            oracle = VerifierOracle("linked", (sys.executable, "-c", "pass"), cwd="linked")
            with patch("code_agent.evaluation.verifier.is_link_or_reparse", return_value=True):
                with self.assertRaisesRegex(ValueError, "workspace traverses a link"):
                    await SubprocessVerifier()(workspace, oracle, "baseline")
            with patch(
                "code_agent.evaluation.paths.is_link_or_reparse",
                side_effect=lambda path: path == linked_cwd,
            ):
                with self.assertRaisesRegex(ValueError, "verifier cwd traverses a link"):
                    await SubprocessVerifier()(workspace, oracle, "baseline")

    async def test_hidden_baseline_is_removed_and_final_uses_snapshot(self) -> None:
        phases: list[tuple[str, Path]] = []

        async def verifier(workspace: Path, _oracle: object, phase: str) -> CommandOutcome:
            phases.append((phase, workspace))
            return CommandOutcome(1 if phase == "baseline" else 0)

        with tempfile.TemporaryDirectory() as temporary:
            scenario = _scenario(Path(temporary), "python-01")

            async def execute(workspace: Path, _prompt: object, recorder: object) -> ScenarioResult:
                self.assertFalse((workspace.parent / "baseline-verifiers").exists())
                self.assertFalse((workspace / "hidden_tests").exists())
                apply_hidden_golden(workspace, scenario)
                return successful_result(scenario, recorder)

            result, observation = await ScenarioRunner(verifier).run(scenario, execute)
        self.assertTrue(observation.baseline_clones_removed_before_execution)
        self.assertTrue(observation.final_snapshot_frozen)
        self.assertTrue(any("final-verifiers" in path.parts for phase, path in phases if phase == "final"))
        self.assertTrue(DeterministicGrader().grade(scenario, result, observation).passed)

    async def test_model_self_report_cannot_replace_trusted_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = _scenario(Path(temporary), "python-01")

            async def execute(workspace: Path, _prompt: object) -> ScenarioResult:
                apply_hidden_golden(workspace, scenario)
                return successful_result(scenario)

            result, observation = await ScenarioRunner(deterministic_verifier).run(scenario, execute)
        failures = DeterministicGrader().grade(scenario, result, observation).failures
        self.assertIn("trusted execution trace is incomplete", failures)
        self.assertIn("unexpected task status", failures)

    async def test_equivalent_source_fix_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = _scenario(Path(temporary), "python-01")

            async def execute(workspace: Path, _prompt: object, recorder: object) -> ScenarioResult:
                alternative = "OFFSET = 1\n\ndef normalize(value):\n    return sum((value, OFFSET))\n"
                (workspace / "calculator.py").write_text(alternative, encoding="utf-8")
                return successful_result(scenario, recorder)

            result, observation = await ScenarioRunner().run(scenario, execute)
        self.assertNotIn("calculator.py", scenario.expected.workspace.exact_files)
        self.assertTrue(DeterministicGrader().grade(scenario, result, observation).passed)

    async def test_parent_write_is_observed_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = _scenario(Path(temporary), "python-01")

            async def execute(workspace: Path, _prompt: object, recorder: object) -> ScenarioResult:
                apply_hidden_golden(workspace, scenario)
                (workspace.parent / "escaped.txt").write_text("escape", encoding="utf-8")
                return successful_result(scenario, recorder)

            result, observation = await ScenarioRunner(deterministic_verifier).run(scenario, execute)
        self.assertIn("escaped.txt", observation.outside_workspace_paths)
        failures = DeterministicGrader().grade(scenario, result, observation).failures
        self.assertIn("write escaped workspace boundary", failures)


class TimeoutAndInfrastructureTests(unittest.IsolatedAsyncioTestCase):
    async def test_swallowed_cancellation_remains_a_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = _empty_scenario(Path(temporary), max_seconds=1)

            async def execute(_workspace: Path, _prompt: object, recorder: object) -> ScenarioResult:
                try:
                    await asyncio.sleep(3)
                except asyncio.CancelledError:
                    _record_empty_success(recorder)
                    return ScenarioResult("completed")
                raise AssertionError("unreachable")

            result, observation = await ScenarioRunner().run(scenario, execute)
        self.assertEqual(result.task_status, "timed_out")
        self.assertTrue(observation.timed_out)
        self.assertTrue(observation.termination_confirmed)
        self.assertFalse(DeterministicGrader().grade(scenario, result, observation).passed)

    async def test_uncooperative_in_process_executor_fails_closed(self) -> None:
        release = asyncio.Event()
        with tempfile.TemporaryDirectory() as temporary:
            scenario = _empty_scenario(Path(temporary), max_seconds=1)

            async def execute(_workspace: Path, _prompt: object) -> ScenarioResult:
                try:
                    await asyncio.sleep(3)
                except asyncio.CancelledError:
                    await release.wait()
                    return ScenarioResult("completed")
                raise AssertionError("unreachable")

            result, observation = await ScenarioRunner().run(scenario, execute)
            release.set()
            await asyncio.sleep(0)
            shutil.rmtree(observation.workspace_path.parent, ignore_errors=True)
        self.assertEqual(result.task_status, "timed_out")
        self.assertFalse(observation.termination_confirmed)
        self.assertFalse(observation.workspace_removed)

    async def test_missing_toolchain_is_infrastructure_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            expected = ScenarioExpectedOutcome(
                "completed",
                workspace=WorkspaceOracle(allowed_changes=()),
                verifiers=(VerifierOracle("missing", ("chaos-toolchain-does-not-exist",)),),
            )
            scenario = Scenario("missing-tool", Path(temporary), "Verify", expected)

            async def execute(_workspace: Path, _prompt: object, recorder: object) -> ScenarioResult:
                _record_empty_success(recorder)
                return ScenarioResult("completed")

            result, observation = await ScenarioRunner().run(scenario, execute)
        self.assertTrue(observation.infrastructure_failures)
        failures = DeterministicGrader().grade(scenario, result, observation).failures
        self.assertTrue(any("missing executable" in failure for failure in failures))

    async def test_process_adapter_has_killable_timeout_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = _empty_scenario(Path(temporary), max_seconds=1)
            execute = ProcessScenarioExecutor(
                trusted_adapter_argv=(sys.executable, "-c", "import time; time.sleep(30)"),
            )
            result, observation = await ScenarioRunner().run(scenario, execute)
        self.assertEqual(result.task_status, "timed_out")
        self.assertEqual(observation.isolation_mode, "subprocess-tree")
        self.assertTrue(observation.termination_confirmed)
        self.assertTrue(observation.workspace_removed)

    async def test_process_adapter_records_trusted_envelope(self) -> None:
        envelope = {
            "result": {"task_status": "completed"},
            "trace": {
                "task_status": "completed",
                "policy_events": [],
                "model_turns": 1,
                "tool_calls": 0,
                "evidence_generation": 0,
                "lifecycle_events": [],
            },
        }
        code = f"import sys; sys.stdin.read(); print({json.dumps(json.dumps(envelope))})"
        with tempfile.TemporaryDirectory() as temporary:
            scenario = _empty_scenario(Path(temporary), max_seconds=5)
            execute = ProcessScenarioExecutor(
                trusted_adapter_argv=(sys.executable, "-c", code),
            )
            result, observation = await ScenarioRunner().run(scenario, execute)
        self.assertEqual(observation.isolation_mode, "subprocess-tree")
        self.assertTrue(DeterministicGrader().grade(scenario, result, observation).passed)

    async def test_process_adapter_stops_on_first_over_limit_byte(self) -> None:
        code = "import sys,time; sys.stdin.read(); sys.stdout.write('x'*4096); sys.stdout.flush(); time.sleep(30)"
        with tempfile.TemporaryDirectory() as temporary:
            scenario = _empty_scenario(Path(temporary), max_seconds=5)
            execute = ProcessScenarioExecutor(
                trusted_adapter_argv=(sys.executable, "-c", code),
                max_output_bytes=128,
            )
            result, observation = await ScenarioRunner().run(scenario, execute)
        self.assertEqual(result.task_status, "executor_failed")
        self.assertTrue(observation.termination_confirmed)
        self.assertTrue(observation.workspace_removed)
        self.assertTrue(
            any("output exceeded" in failure for failure in observation.infrastructure_failures)
        )


class ModelBoundaryTests(unittest.TestCase):
    def test_windows_unc_and_rooted_oracle_paths_are_rejected(self) -> None:
        invalid = (r"C:\secret.txt", "C:secret.txt", r"\\server\share\x", "/rooted", "../up")
        for path in invalid:
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    WorkspaceOracle(exact_files={path: "x"})
                with self.assertRaises(ValueError):
                    VerifierOracle("hidden", ("python",), hidden_files={path: "x"})

    def test_category_fifth_positional_argument_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            expected = ScenarioExpectedOutcome("completed", workspace=WorkspaceOracle())
            scenario = Scenario("position", Path(temporary), "Inspect", expected, "safety")
        self.assertEqual(scenario.category, "safety")
        self.assertEqual(scenario.fixture_version, "fixture-v1")

    def test_dotnet_hidden_projects_enable_implicit_usings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = _scenario(Path(temporary), "dotnet-01")
        hidden = next(item for item in scenario.expected.verifiers if item.name == "dotnet_hidden")
        self.assertIn("<ImplicitUsings>enable</ImplicitUsings>", hidden.hidden_files["hidden/Hidden.csproj"])


def _scenario(root: Path, identifier: str) -> Scenario:
    return next(item for item in fixed_replay_catalog(root) if item.identifier == identifier)


def _empty_scenario(root: Path, *, max_seconds: int) -> Scenario:
    expected = ScenarioExpectedOutcome("completed", workspace=WorkspaceOracle(allowed_changes=()))
    return Scenario("timeout-hardening", root, "Complete without writes", expected, max_active_seconds=max_seconds)


def _record_empty_success(recorder: object) -> None:
    recorder.record_status("completed")
    recorder.record_budget(model_turns=1, tool_calls=0)
    recorder.record_evidence_generation(0)
    recorder.seal()


if __name__ == "__main__":
    unittest.main()
