from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.evaluation.benchmark import ReplayBenchmark
from code_agent.evaluation.catalog import fixed_replay_catalog
from code_agent.evaluation.runner import ScenarioRunner

from helpers import apply_hidden_golden, deterministic_verifier, successful_result


class BenchmarkExperienceMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_benchmark_result_exposes_trusted_experience_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = fixed_replay_catalog(root)[:1]
            scenario = catalog[0]

            async def execute(workspace: Path, _prompt: object, recorder: object):
                apply_hidden_golden(workspace, scenario)
                return successful_result(scenario, recorder)

            result = await ReplayBenchmark(runner=ScenarioRunner(deterministic_verifier)).run(catalog, execute)
        self.assertEqual(result.experience_metrics.total, 1)
        self.assertEqual(result.experience_metrics.verified, 1)
        self.assertEqual(result.experience_metrics.false_completions, 0)
        self.assertIn("experience_metrics", result.to_json())
        self.assertIn("Verified: 1/1", result.to_markdown())

