import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from semantic_harness import audit_request, freeze_cases, request_for, run_pair
from semantic_oracle import observe_case, score_reply


class SemanticHarnessTests(unittest.TestCase):
    def setUp(self):
        self.cases = freeze_cases()

    def test_identity_is_not_mutation(self):
        result = observe_case(self.cases[1])
        self.assertTrue(result["implementation_compliance"]["no_input_mutation"])
        self.assertFalse(result["copy_observation"]["distinct_object"])

    def test_actual_request_isolation_and_tamper_rejection(self):
        case = self.cases[0]
        body = request_for(case, "B", 1)
        audit_request(case, "B", 1, body)
        payload = json.loads(body["messages"][1]["content"])
        self.assertNotIn("child_advisory", payload)
        payload["summary"] = case["child_advisory"]
        body["messages"][1]["content"] = json.dumps(payload)
        with self.assertRaises(ValueError):
            audit_request(case, "B", 1, body)
        body = request_for(case, "B", 1)
        payload = json.loads(body["messages"][1]["content"])
        payload["sources"][0]["text"] = "truncated"
        body["messages"][1]["content"] = json.dumps(payload)
        with self.assertRaises(ValueError):
            audit_request(case, "B", 1, body)

    def test_test_discrimination_matrix(self):
        result = observe_case(self.cases[0])
        matrix = result["counterexample_matrix"]
        self.assertFalse(matrix["returns_none"]["test_empty"]["pass"])
        self.assertTrue(matrix["sorts_cleaned"]["test_duplicates"]["pass"])
        self.assertTrue(matrix["sorts_cleaned"]["test_trim"]["pass"])
        self.assertTrue(all(t["pass"] for t in matrix["sorts_cleaned"].values()))
        self.assertFalse(matrix["reverses_cleaned"]["test_trim"]["pass"])
        self.assertFalse(matrix["deduplicates"]["test_duplicates"]["pass"])
        self.assertFalse(matrix["mutates_input"]["test_no_mutation"]["pass"])
        sorted_result = observe_case(self.cases[2])
        self.assertFalse(sorted_result["implementation_compliance"]["preserve_order"])
        self.assertTrue(all(t["pass"] for t in sorted_result["source_tests"].values()))

    def test_both_arms_two_equal_budget_requests(self):
        calls = []
        async def send(body):
            calls.append(body)
            return {"text": "{}", "usage": {"completion_tokens": 1}}
        with tempfile.TemporaryDirectory() as directory:
            for arm in ("A", "B"):
                asyncio.run(run_pair(self.cases[0], arm, Path(directory) / arm, send))
        self.assertEqual(len(calls), 4)
        self.assertEqual({b["max_tokens"] for b in calls}, {4096})
        self.assertEqual({b["reasoning_effort"] for b in calls}, {"medium"})

    def test_failure_preserved_no_retry(self):
        calls = []
        async def fail(body):
            calls.append(body)
            raise TimeoutError("scripted transport failure")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "failed"
            with self.assertRaises(TimeoutError):
                asyncio.run(run_pair(self.cases[0], "B", output, fail))
            result = json.loads((output / "result.json").read_text())
            self.assertEqual(result["status"], "FAILED_PRESERVED")
            self.assertEqual(len(calls), 1)
            self.assertTrue((output / "request-1.json").is_file())

    def test_score_unknown_and_false_acceptance_separately(self):
        scored = score_reply(self.cases[0], '{"compliance":{"trim":true,"no_input_mutation":null}}')
        self.assertEqual(scored["observations"]["trim"]["label"], "false_acceptance")
        self.assertEqual(scored["observations"]["no_input_mutation"]["label"], "unknown_or_missing")
        self.assertEqual(score_reply(self.cases[0], "no JSON")["status"], "UNPARSEABLE_PRESERVED")


if __name__ == "__main__":
    unittest.main()
