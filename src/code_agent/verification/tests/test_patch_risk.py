from pathlib import Path
import tempfile
import unittest
from code_agent.verification.planner import VerificationPlanner, VerificationPhase
from code_agent.verification.risk import RiskTier, classify_patch_risk


class PatchRiskTests(unittest.TestCase):
    def test_comment_does_not_escalate_on_protocol_words(self):
        risk = classify_patch_risk(["helper.py"], "-# old\n+# protocol & parser comment")
        self.assertEqual(risk[0], RiskTier.LOW)
        self.assertEqual(risk[3], ())

    def test_condition_is_medium(self):
        self.assertEqual(classify_patch_risk(["helper.py"], "-if x:\n+if x > 1:")[0], RiskTier.MEDIUM)

    def test_removed_bitwise_and_shift_are_high(self):
        for diff in ("-return flags & mask\n+return flags", "+return value >> width", "+def public(value, mode):"):
            self.assertEqual(classify_patch_risk(["helper.py"], diff)[0], RiskTier.HIGH)

    def test_existing_related_behavior_tests_selected_and_final_gate_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            for name in ("test_helper.py", "test_helper_boundaries.py"):
                (root / "tests" / name).write_text("", encoding="utf-8")
            plan = VerificationPlanner(root).plan(["helper.py"], VerificationPhase.FINAL_GATE,
                diff="@@ -1 +1 @@ def helper(value):\n-return value\n+return value & 7")
            self.assertEqual(plan.tier, RiskTier.HIGH)
            self.assertTrue(plan.require_full_gate)
            self.assertEqual(len(plan.targeted_tests), 2)
            self.assertIn("helper", plan.changed_symbols)
            self.assertIn("final_project_gate", plan.additional_checks)
            self.assertEqual(plan.to_dict()["selected_tests"], list(plan.targeted_tests))

    def test_truncated_patch_falls_back_to_high(self):
        self.assertEqual(classify_patch_risk(["helper.py"], "+# PATCH_BODY_TRUNCATED")[0], RiskTier.HIGH)
