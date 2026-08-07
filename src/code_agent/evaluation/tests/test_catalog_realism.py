from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from pathlib import Path

from code_agent.evaluation.catalog import EXPECTED_CATEGORY_COUNTS, fixed_replay_catalog


class CatalogRealismTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog = fixed_replay_catalog(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_catalog_is_exactly_40_unique_with_fixed_quotas(self) -> None:
        self.assertEqual(len(self.catalog), 40)
        self.assertEqual(len({scenario.identifier for scenario in self.catalog}), 40)
        self.assertEqual(Counter(item.category for item in self.catalog), EXPECTED_CATEGORY_COUNTS)

    def test_prompt_exposes_no_hidden_oracle(self) -> None:
        prompt = self.catalog[0].prompt()
        self.assertFalse(hasattr(prompt, "expected"))
        self.assertFalse(hasattr(prompt, "fixture_files"))
        self.assertFalse(hasattr(prompt, "workspace"))
        self.assertTrue(prompt.fixture_version.startswith("catalog-v3/"))

    def test_repair_fixtures_have_real_public_and_hidden_checks(self) -> None:
        repairs = [item for item in self.catalog if item.category != "safety"]
        self.assertTrue(all(len(item.expected.verifiers) == 2 for item in repairs))
        for scenario in repairs:
            public_paths = set(scenario.fixture_files)
            hidden_paths = {
                path
                for verifier in scenario.expected.verifiers
                for path in verifier.hidden_files
            }
            self.assertTrue(hidden_paths)
            self.assertTrue(public_paths.isdisjoint(hidden_paths))
            self.assertTrue(all(verifier.baseline_must_fail for verifier in scenario.expected.verifiers))
            self.assertTrue(scenario.expected.workspace.required_changes)

    def test_generated_assets_are_not_placeholders(self) -> None:
        corpus = "\n".join(
            content
            for scenario in self.catalog
            for content in scenario.fixture_files.values()
        )
        self.assertNotIn("assert True", corpus)
        self.assertNotIn("console.log('ok')", corpus)
        self.assertIn("node:test", corpus)
        self.assertIn("TargetFramework", corpus)
        self.assertIn("public contract failed", corpus)

    def test_broken_sources_differ_from_hidden_golden(self) -> None:
        for scenario in self.catalog:
            for path, golden in scenario.golden_files.items():
                if path in scenario.expected.workspace.required_changes:
                    self.assertNotEqual(scenario.fixture_files[path], golden)

    def test_source_golden_is_not_a_grading_oracle(self) -> None:
        for scenario in self.catalog:
            if scenario.category in ("bugfix", "multifile", "recovery"):
                source_golden = set(scenario.golden_files) - {"state.json", "ledger.json"}
                self.assertTrue(source_golden.isdisjoint(scenario.expected.workspace.exact_files))

    def test_safety_group_contains_completed_read_only_work(self) -> None:
        read_only = next(item for item in self.catalog if item.identifier == "security-08")
        self.assertEqual(read_only.expected.task_status, "completed")
        self.assertEqual(read_only.expected.workspace.allowed_changes, ())
        self.assertIn("analysis_completed", read_only.expected.lifecycle.required_events)

    def test_recovery_hidden_verifier_replays_resume_twice(self) -> None:
        recovery = next(item for item in self.catalog if item.identifier == "windows_resume-01")
        hidden = next(item for item in recovery.expected.verifiers if item.name == "python_hidden")
        source = "\n".join(hidden.hidden_files.values())
        self.assertEqual(source.count("subprocess.check_call"), 2)
        self.assertIn("ledger", source)

    def test_groups_include_distinct_behavior_shapes(self) -> None:
        sources = "\n".join(self._by_id(identifier).fixture_files.get("calculator.py", "") for identifier in ("python-01", "python-02"))
        self.assertIn("OFFSET", sources)
        self.assertIn("LIMIT", sources)
        additive = self._by_id("python-multifile-02").fixture_files["service.py"]
        multiplicative = self._by_id("python-multifile-01").fixture_files["service.py"]
        self.assertIn("BIAS", additive)
        self.assertIn("MULTIPLIER", multiplicative)
        self.assertNotEqual(
            self._by_id("windows_resume-01").expected.lifecycle.required_events,
            self._by_id("windows_resume-06").expected.lifecycle.required_events,
        )

    def _by_id(self, identifier: str):
        return next(item for item in self.catalog if item.identifier == identifier)


if __name__ == "__main__":
    unittest.main()
