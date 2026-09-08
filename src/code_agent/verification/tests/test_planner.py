from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.verification.models import VerificationKind, VerificationRequest
from code_agent.verification.planner import (
    PlannerVerificationAdapter,
    RiskTier,
    SyntaxCheckResult,
    VerificationPhase,
    VerificationPlan,
    VerificationPlanner,
    check_syntax,
    classify_risk,
)


class VerificationPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace_root = SRC_ROOT.parent
        self.planner = VerificationPlanner(self.workspace_root)
        self.adapter = PlannerVerificationAdapter(self.planner)

    def test_syntax_check_valid_python(self) -> None:
        code = "def hello():\n    return 'world'\n"
        result = check_syntax("sample.py", code)
        self.assertTrue(result.is_valid)
        self.assertIn("syntax OK", result.format_diagnostic())

    def test_syntax_check_invalid_python(self) -> None:
        code = "def broken(:\n    return\n"
        result = check_syntax("broken.py", code)
        self.assertFalse(result.is_valid)
        self.assertIsNotNone(result.line)
        self.assertIn("broken.py:1", result.format_diagnostic())

    def test_syntax_check_indentation_error(self) -> None:
        code = "def indent():\nreturn 1\n"
        result = check_syntax("indent.py", code)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.line, 2)

    def test_syntax_check_json(self) -> None:
        valid_json = '{"name": "test", "count": 1}'
        invalid_json = '{"name": "test", "count": }'
        self.assertTrue(check_syntax("data.json", valid_json).is_valid)
        bad = check_syntax("data.json", invalid_json)
        self.assertFalse(bad.is_valid)
        self.assertIsNotNone(bad.line)

    def test_classify_risk_low(self) -> None:
        files = ["README.md", "docs/architecture.md", "notes.txt"]
        tier, reason = classify_risk(files)
        self.assertEqual(tier, RiskTier.LOW)
        self.assertIn("documentation", reason)

    def test_classify_risk_critical(self) -> None:
        files = ["pyproject.toml", "src/code_agent/sessions/_database.py"]
        tier, reason = classify_risk(files)
        self.assertEqual(tier, RiskTier.CRITICAL)
        self.assertIn("Critical", reason)

    def test_classify_risk_high(self) -> None:
        files = ["src/code_agent/core/engine.py", "src/code_agent/workspace/files.py"]
        tier, reason = classify_risk(files)
        self.assertEqual(tier, RiskTier.HIGH)
        self.assertIn("Core subsystem", reason)

    def test_classify_risk_medium(self) -> None:
        files = ["src/code_agent/attachments/ingest.py"]
        tier, reason = classify_risk(files)
        self.assertEqual(tier, RiskTier.MEDIUM)

    def test_plan_in_flight_phase(self) -> None:
        files = ["src/code_agent/verification/models.py"]
        plan = self.planner.plan(files, phase=VerificationPhase.IN_FLIGHT)
        self.assertEqual(plan.phase, VerificationPhase.IN_FLIGHT)
        self.assertTrue(plan.skip_tests)
        self.assertEqual(plan.syntax_targets, tuple(files))
        self.assertEqual(plan.targeted_tests, ())

    def test_plan_low_risk_skips_tests(self) -> None:
        files = ["README.md", "LICENSE"]
        plan = self.planner.plan(files, phase=VerificationPhase.FINAL_GATE)
        self.assertEqual(plan.tier, RiskTier.LOW)
        self.assertTrue(plan.skip_tests)
        self.assertFalse(plan.require_full_gate)

    def test_plan_critical_risk_requires_full_gate_on_final(self) -> None:
        files = ["pyproject.toml"]
        plan = self.planner.plan(files, phase=VerificationPhase.FINAL_GATE)
        self.assertEqual(plan.tier, RiskTier.CRITICAL)
        self.assertFalse(plan.skip_tests)
        self.assertTrue(plan.require_full_gate)

    def test_plan_impacted_tests_identification(self) -> None:
        # Direct test file
        files = ["src/code_agent/verification/tests/test_planner.py"]
        impacted = self.planner.find_impacted_tests(files)
        self.assertIn("src/code_agent/verification/tests/test_planner.py", impacted)

        # Source file with sibling test directory
        source_files = ["src/code_agent/verification/models.py"]
        impacted_source = self.planner.find_impacted_tests(source_files)
        self.assertTrue(any("verification/tests" in target for target in impacted_source))

    def test_adapter_syntax_error_aborts_execution(self) -> None:
        plan = VerificationPlan(
            tier=RiskTier.MEDIUM,
            phase=VerificationPhase.LOCAL_MILESTONE,
            changed_files=("bad.py",),
            syntax_targets=("bad.py",),
            targeted_tests=("tests/test_bad.py",),
            require_full_gate=False,
            skip_tests=False,
            reason="Local edit",
        )
        outcome = self.adapter.prepare_request(
            plan, default_kind=VerificationKind.PYTHON_UNITTEST
        )
        # File does not exist on disk, so syntax check reports file error
        self.assertFalse(outcome.syntax_passed)
        self.assertTrue(outcome.tests_skipped)
        self.assertIsNone(outcome.request)
        self.assertIn("Syntax check failed", outcome.diagnostic)

    def test_adapter_low_risk_skips_cleanly(self) -> None:
        plan = self.planner.plan(["README.md"], phase=VerificationPhase.FINAL_GATE)
        outcome = self.adapter.prepare_request(plan)
        self.assertTrue(outcome.syntax_passed)
        self.assertTrue(outcome.tests_skipped)
        self.assertIsNone(outcome.request)
        self.assertIn("bypassed safely", outcome.diagnostic)


if __name__ == "__main__":
    unittest.main()
