from __future__ import annotations

import unittest

from code_agent.evaluation.experience_metrics import (
    ExperienceEvaluationMetrics,
    ExperienceTaskRecord,
    FailureSource,
    render_experience_metrics,
)


class ExperienceMetricsTests(unittest.TestCase):
    def test_aggregates_completion_and_failure_layers(self) -> None:
        records = (
            ExperienceTaskRecord("fix-test", "bugfix", True, True, model_tokens=100, tool_calls=2),
            ExperienceTaskRecord("provider-down", "bugfix", False, False, retries=1, failure_source=FailureSource.PROVIDER),
            ExperienceTaskRecord("misreported", "migration", True, False, false_completion=True, failure_source=FailureSource.AGENT),
        )
        metrics = ExperienceEvaluationMetrics.from_records(records)
        self.assertEqual(metrics.completed, 2)
        self.assertEqual(metrics.verified, 1)
        self.assertEqual(metrics.false_completions, 1)
        self.assertEqual(metrics.failures_by_source["provider"], 1)
        self.assertIn("False completions: 1", render_experience_metrics(metrics))

    def test_unverified_completion_must_be_explicitly_marked(self) -> None:
        with self.assertRaises(ValueError):
            ExperienceTaskRecord("bad", "bugfix", True, False)

