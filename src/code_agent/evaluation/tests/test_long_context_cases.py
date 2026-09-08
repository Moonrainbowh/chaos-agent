import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from code_agent.evaluation.long_context_cases import long_context_cases, pilot_case


class LongCaseTests(unittest.TestCase):
    def test_ten_distinct_contracts_have_real_failures_and_separate_pilot(self):
        cases = long_context_cases()
        self.assertEqual(len({c.identifier for c in cases}), 10)
        self.assertNotIn(pilot_case().identifier, {c.identifier for c in cases})
        for case in (*cases, pilot_case()):
            namespace = {}
            exec(case.faulty_source, namespace)
            results = [namespace["solve"](item["input"]) != item["expected"] for item in case.oracle()]
            self.assertTrue(any(results), case.identifier)
