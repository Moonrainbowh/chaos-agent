from pathlib import Path
import tempfile
import unittest
from code_agent.verification.planner import VerificationPlanner, VerificationPhase
from code_agent.verification.risk import RiskTier, classify_patch_risk


class PatchRiskTests(unittest.TestCase):
    def test_comment_does_not_escalate_on_protocol_words(self):
        risk = classify_patch_risk(["helper.py"], "-# old\n+# protocol & parser comment")
        self.assertEqual(risk[0], RiskTier.TRIVIAL)
        self.assertEqual(risk[3], ())

    def test_single_file_twenty_line_patch_is_trivial(self):
        diff = "\n".join(
            [*(f"-VALUE_{index} = 1" for index in range(10)),
             *(f"+VALUE_{index} = 2" for index in range(10))]
        )
        risk = classify_patch_risk(["helper.py"], diff)
        self.assertEqual(risk[0], RiskTier.TRIVIAL)
        self.assertTrue(any("20 changed lines" in reason for reason in risk[2]))

    def test_one_line_static_asset_is_trivial_but_embedded_script_is_not(self):
        self.assertEqual(
            classify_patch_risk(["icon.svg"], "+<text>Ready</text>")[0],
            RiskTier.TRIVIAL,
        )
        self.assertNotEqual(
            classify_patch_risk(["icon.svg"], "+<script>start()</script>")[0],
            RiskTier.TRIVIAL,
        )

    def test_twenty_first_line_and_second_file_restore_medium_risk(self):
        twenty_one_lines = "\n".join(
            [*(f"-VALUE_{index} = 1" for index in range(10)),
             *(f"+VALUE_{index} = 2" for index in range(11))]
        )
        small_diff = "-VALUE = 1\n+VALUE = 2"
        self.assertEqual(
            classify_patch_risk(["helper.py"], twenty_one_lines)[0],
            RiskTier.MEDIUM,
        )
        self.assertEqual(
            classify_patch_risk(["helper.py", "labels.py"], small_diff)[0],
            RiskTier.MEDIUM,
        )

    def test_condition_is_medium(self):
        self.assertEqual(classify_patch_risk(["helper.py"], "-if x:\n+if x > 1:")[0], RiskTier.MEDIUM)

    def test_trivial_candidate_risk_signals_restore_verification(self):
        cases = {
            "import": "+from package import public_api",
            "class": "+class Service:",
            "security": "+token = authenticate(user)",
            "persistence": "+payload = serialize(record)",
            "concurrency": "+lock.acquire()",
            "network": "+response = socket.recv(10)",
        }
        for label, diff in cases.items():
            with self.subTest(label=label):
                self.assertNotEqual(
                    classify_patch_risk(["feature.py"], diff)[0],
                    RiskTier.TRIVIAL,
                )

    def test_test_config_entry_and_high_risk_paths_are_not_trivial(self):
        diff = "-VALUE = 1\n+VALUE = 2"
        cases = {
            "test": "tests/test_helper.py",
            "config": "settings.json",
            "entry": "main.py",
            "high": "src/code_agent/core/constants.py",
        }
        for label, path in cases.items():
            with self.subTest(label=label):
                self.assertNotEqual(
                    classify_patch_risk([path], diff)[0],
                    RiskTier.TRIVIAL,
                )

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
