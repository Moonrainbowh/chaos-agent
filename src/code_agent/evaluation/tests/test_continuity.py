import asyncio
from dataclasses import replace
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from code_agent.evaluation.continuity_driver import Receipt, _check
from code_agent.evaluation.continuity_fixture import fixture_files
from code_agent.evaluation.continuity_plan import events
from code_agent.evaluation.continuity_selfcheck import selfcheck


class ContinuityTests(unittest.TestCase):
    def test_reference_and_negative_controls(self):
        report = asyncio.run(selfcheck())
        self.assertTrue(report["selfcheck_passed"], report)
        self.assertEqual(report["real_api_status"], "NOT_RUN")
        stale = next(r for r in report["negative_controls"] if r["mutation"] == "stale-validation")
        self.assertTrue(stale["behavior_and_workspace_passed"])
        self.assertFalse(stale["fresh_scripted_verification"])

    def test_plans_and_no_hidden_material(self):
        for arm in "ABCD":
            plan = events(arm)
            self.assertEqual(sum(e.action == "work" for e in plan), 4)
            self.assertEqual(sum(e.action == "switch" for e in plan), 0 if arm == "A" else 3)
        d = [e.action for e in events("D")]
        self.assertLess(d.index("pause"), d.index("patch"))
        self.assertLess(d.index("patch"), d.index("resume"))
        self.assertNotIn("_continuity_hidden.py", fixture_files())
        with self.assertRaises(ValueError):
            events("E")

    def test_host_receipts_reject_unperformed_actions(self):
        original = Receipt("task", "process-1", "window-1", "store")
        for action, receipt in (("switch", original), ("pause", original),
                                ("resume", original),
                                ("work", replace(original, task_id="other")),
                                ("work", replace(original, store_id="other")),
                                ("work", replace(original, tools_settled=False))):
            with self.subTest(action=action, receipt=receipt), self.assertRaises(ValueError):
                _check(original, receipt, action)
        paused = replace(original, process_exited=True)
        _check(original, paused, "pause")
        _check(paused, replace(original, process_instance="process-2"), "resume")
        _check(original, replace(original, window_id="window-2"), "switch")


if __name__ == "__main__":
    unittest.main()
