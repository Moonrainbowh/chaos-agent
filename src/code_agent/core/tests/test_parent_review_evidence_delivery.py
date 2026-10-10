"""Literal witnesses feed review delivery without becoming execution proof."""
import json
import unittest

from code_agent.core.parent_review import ParentReviewSnapshot, evaluate_response, render_delivery


class EvidenceDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.data = {'protocol_version': 3, 'phase': 'independent', 'objective': 'Review sources.',
            'requirements': [{'id': 'test_discrimination'}],
            'sources': [{'path': 'checks.py', 'version': 'frozen', 'text': 'assert observed == expected\n'}],
            'advisory': 'CHILD_SECRET', 'child_objective': 'CHILD_SECRET'}
        self.citation = {'path': 'checks.py', 'start_line': 1, 'end_line': 1}
        self.draft = {'findings': [{'requirement_id': 'test_discrimination',
            'judgment': '只描述列出的具体见证。', 'citations': [self.citation]}],
            'assertion_checks': [{'id': 'T1', 'citations': [self.citation], 'input': [' Q ', 'Q'],
                'observed': '返回值', 'expected': ['Q', 'Q'],
                'current': {'actual': [' Q ', 'Q'], 'trace': '复制原值。'},
                'detected': {'actual': ['Q'], 'fault': 'trim 后去重。'},
                'missed': {'actual': ['Q', 'Q'], 'fault': 'trim 后排序。',
                    'counterexample': {'input': ['Z', 'Q'], 'expected': ['Z', 'Q'], 'actual': ['Q', 'Z']}},
                'unknowns': []}], 'unknowns': [], 'comparisons': []}

    def evaluate(self, draft=None):
        return evaluate_response(json.dumps(draft or self.draft), ParentReviewSnapshot(self.data), comparison=False)

    def test_v3_requires_witnesses_and_v2_remains_resumable(self):
        self.draft.pop('assertion_checks')
        self.assertTrue(self.evaluate()[1])
        self.data['protocol_version'] = 2
        self.assertFalse(self.evaluate()[1])

    def test_comparison_receives_host_calculated_outcome_and_chinese_instruction(self):
        parsed, errors, _ = self.evaluate()
        self.assertFalse(errors)
        self.assertEqual(parsed['assertion_checks'][0]['current_outcome'], 'fail')
        self.assertEqual(parsed['assertion_checks'][0]['scope'], 'witness_only')
        snapshot = ParentReviewSnapshot(self.data)
        self.assertNotIn('CHILD_SECRET', snapshot.bundle().messages[0].content)
        self.assertIn('Simplified Chinese', snapshot.bundle().system_prompt)
        self.data.update(phase='comparison', initial=json.dumps(parsed))
        payload = json.loads(ParentReviewSnapshot(self.data).bundle().messages[0].content)
        self.assertEqual(json.loads(payload['initial'])['assertion_checks'][0]['current_outcome'], 'fail')

    def test_false_passing_witness_is_repaired_without_rewriting_valid_findings(self):
        self.draft['assertion_checks'][0]['missed']['actual'] = [' Q ', 'Q']
        raw = json.dumps(self.draft)
        errors = self.evaluate()[1]
        self.assertTrue(errors)
        self.assertIn('/assertion_checks/0/missed', [e['path'] for e in errors])
        self.data.update(repair=list(errors), repair_response=raw, repair_phase='independent')
        prompt = ParentReviewSnapshot(self.data).bundle().system_prompt
        self.assertIn('Return ONLY a JSON patch object', prompt)
        self.assertNotIn('also include "assertion_checks"', prompt)
        fixed = dict(self.draft['assertion_checks'][0]['missed'], actual=['Q', 'Q'])
        patch = json.dumps({'replacements': [{'path': '/assertion_checks/0/missed', 'value': fixed}]})
        parsed, errors, effective = evaluate_response(patch, ParentReviewSnapshot(self.data), comparison=False)
        self.assertFalse(errors)
        self.assertEqual(json.loads(effective)['findings'], self.draft['findings'])
        self.assertEqual(parsed['assertion_checks'][0]['missed']['actual'], ['Q', 'Q'])

    def test_render_exposes_values_and_finite_scope_not_json_or_execution_claim(self):
        parsed, errors, _ = self.evaluate()
        self.assertFalse(errors)
        rendered = render_delivery(parsed)
        self.assertIn('[" Q ", "Q"]', rendered)
        self.assertIn('["Q", "Q"]', rendered)
        self.assertIn('该见证被拒绝', rendered)
        self.assertIn('该见证满足断言', rendered)
        self.assertIn('不能推出所有错误实现', rendered)
        self.assertIn('结论未经过运行验证', rendered)
        self.assertNotIn('"assertion_checks"', rendered)


if __name__ == '__main__':
    unittest.main()
