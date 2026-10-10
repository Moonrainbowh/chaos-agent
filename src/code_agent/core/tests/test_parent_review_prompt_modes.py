"""Each parent review request exposes exactly the response protocol it accepts."""
import json
import unittest

from code_agent.core.parent_review import ParentReviewSnapshot, evaluate_response


class ParentReviewPromptModeTests(unittest.TestCase):
    def setUp(self):
        self.data = {'protocol_version': 2, 'phase': 'independent', 'objective': 'Review.',
            'child_objective': 'CHILD_SECRET', 'advisory': 'CHILD_SECRET',
            'requirements': [{'id': 'implementation'}],
            'sources': [{'path': 'example.py', 'version': 'frozen', 'text': 'return values\n'}]}
        self.draft = {'findings': [{'requirement_id': 'implementation', 'judgment': 'Copies input.',
            'citations': [{'path': 'example.py', 'start_line': 1, 'end_line': 99}]}],
            'unknowns': [], 'comparisons': []}

    def repair(self, raw):
        snapshot = ParentReviewSnapshot(self.data)
        errors = evaluate_response(raw, snapshot, comparison=False)[1]
        self.assertTrue(errors)
        self.data.update(repair_response=raw, repair=list(errors), repair_phase='independent')
        return ParentReviewSnapshot(self.data).bundle()

    def test_field_repair_has_only_patch_output_contract(self):
        raw = json.dumps(self.draft)
        bundle = self.repair(raw)
        self.assertIn('"replacements"', bundle.system_prompt)
        self.assertNotIn('Return one JSON object: {"findings"', bundle.system_prompt)
        self.assertNotIn('Only if the previous response', bundle.system_prompt)
        body = json.loads(bundle.messages[0].content)
        self.assertEqual(body['repair']['previous_response'], raw)
        self.assertEqual(body['repair']['errors'][0]['path'], '/findings/0/citations/0/end_line')
        self.assertNotIn('CHILD_SECRET', bundle.messages[0].content)
        patch = json.dumps({'replacements': [{'path': '/findings/0/citations/0/end_line', 'value': 1}]})
        self.assertFalse(evaluate_response(patch, ParentReviewSnapshot(self.data), comparison=False)[1])
        # Disclosure changes do not accept a full rewrite or edits outside the error.
        self.draft['findings'][0]['citations'][0]['end_line'] = 1
        self.assertTrue(evaluate_response(json.dumps(self.draft), ParentReviewSnapshot(self.data), comparison=False)[1])

    def test_non_object_repair_has_only_complete_review_contract(self):
        for raw in ('Only a plan.', '[]', '```json\n[]\n```'):
            with self.subTest(raw=raw):
                self.data.pop('repair', None)
                bundle = self.repair(raw)
                self.assertIn('Return one JSON object: {"findings"', bundle.system_prompt)
                self.assertNotIn('"replacements"', bundle.system_prompt)

    def test_initial_and_wrong_phase_request_never_offer_patch_format(self):
        for updates in ({}, {'repair': [{'path': '/unknowns', 'message': 'bad'}],
                            'repair_response': 'CHILD_SECRET', 'repair_phase': 'comparison'}):
            self.data.update(updates)
            bundle = ParentReviewSnapshot(self.data).bundle()
            self.assertIn('Return one JSON object: {"findings"', bundle.system_prompt)
            self.assertNotIn('"replacements"', bundle.system_prompt)
            self.assertNotIn('CHILD_SECRET', bundle.messages[0].content)

    def test_legacy_repair_has_only_complete_review_contract(self):
        self.data.pop('protocol_version')
        bundle = self.repair(json.dumps(self.draft))
        self.assertIn('Return one JSON object: {"findings"', bundle.system_prompt)
        self.assertNotIn('"replacements"', bundle.system_prompt)
        self.assertIn('exact version and quote', bundle.system_prompt)

    def test_stale_heading_only_repair_accepts_only_explicit_empty_patch(self):
        self.data.update(phase='comparison', advisory='# Heading\n\nBody.')
        self.draft['findings'][0]['citations'][0]['end_line'] = 1
        self.draft['comparisons'] = [{'child_claim': 'Body.', 'paragraph_ids': [2],
            'assessment': 'Agreed', 'rationale': 'Static source.',
            'citations': [{'path': 'example.py', 'start_line': 1, 'end_line': 1}]}]
        raw = json.dumps(self.draft)
        self.data.update(repair=[{'path': '/comparisons/-', 'message': 'uncovered paragraphs: [1]'}],
            repair_response=raw, repair_phase='comparison', repair_used=True)
        snapshot = ParentReviewSnapshot(self.data)
        prompt = snapshot.bundle().system_prompt
        self.assertIn('Return ONLY {"replacements":[]}', prompt)
        self.assertNotIn('1..32', prompt)
        self.assertNotIn('Return one JSON object: {"findings"', prompt)
        parsed, errors, effective = evaluate_response('{"replacements":[]}', snapshot, comparison=True)
        self.assertFalse(errors)
        self.assertEqual(json.loads(effective), self.draft)
        self.assertTrue(self.data['repair_used'])
        for bad in (raw, '{"replacements":[],"extra":true}',
                    '{"replacements":[{"path":"/comparisons/-","value":{}}]}'):
            self.assertTrue(evaluate_response(bad, snapshot, comparison=True)[1])
        self.draft['findings'][0]['citations'][0]['end_line'] = 99
        self.data['repair_response'] = json.dumps(self.draft)
        self.assertTrue(evaluate_response('{"replacements":[]}', snapshot, comparison=True)[1])


if __name__ == '__main__':
    unittest.main()
