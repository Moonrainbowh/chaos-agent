import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from code_agent.evaluation.continuity_v2_metrics import action_metrics
from code_agent.evaluation.continuity_v2_plan import events
from code_agent.evaluation.continuity_v2_selfcheck import selfcheck


class ContinuityV2Tests(unittest.TestCase):
    def test_challenge_and_ceiling_controls(self):
        report = asyncio.run(selfcheck())
        self.assertTrue(report["selfcheck_passed"], report)
        compatible = report["negative_controls"][1]
        self.assertTrue(compatible["final_passed"])
        self.assertFalse(compatible["coverage"]["old_solution_invalidated"])
        for row in report["reference"]:
            self.assertEqual(row["coverage"]["status"], "covered")
        for arm in "ABCD":
            self.assertEqual(sum(e.action == "patch" for e in events(arm)), 1)

    def test_lists_failed_reads_and_unchanged_notes_do_not_cover_probe(self):
        def row(name, stage, text="", error=False):
            return {"stage": stage, "request": {"name": name, "arguments": {
                "path": "reader-contract.md", "text": text}}, "result": {"is_error": error}}
        rows = [row("notes_list_files", 2), row("notes_write_file", 2, "tuple"),
                row("notes_read_file", 3, error=True), row("notes_write_file", 4, "record")]
        self.assertEqual(action_metrics(rows, True)["note_lifecycle"], "not-covered")
        rows[2]["result"]["is_error"] = False
        self.assertEqual(action_metrics(rows, True)["note_semantics"], "review-required")
        reordered = [rows[0], rows[1], rows[3], rows[2]]
        self.assertEqual(action_metrics(reordered, True)["note_lifecycle"], "not-covered")
        rows[-1]["request"]["arguments"]["text"] = "tuple"
        self.assertEqual(action_metrics(rows, True)["note_lifecycle"], "not-covered")
        rows.append({**row("write_file", 3), "policy_rejection": "diagnosis-read-only"})
        self.assertEqual(action_metrics(rows, True)["constraint_attempt_count"], 1)
