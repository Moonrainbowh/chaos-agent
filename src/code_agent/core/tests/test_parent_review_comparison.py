"""Comparison deltas preserve independent records and revalidate full delivery."""
import copy
import json
import unittest

from code_agent.core.parent_review import ParentReviewSnapshot, evaluate_response
from code_agent.core._parent_review_comparison import expand_comparison, inspect_comparison_draft


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.cite = {'path': 'example.py', 'start_line': 1, 'end_line': 1}
        self.finding = {'requirement_id': 'implementation', 'judgment': 'Copies input.',
                        'citations': [self.cite]}
        self.check = {'id': 'check', 'citations': [self.cite],
                      'unknown': 'Observation is not represented by ordinary JSON.'}
        self.initial = {'findings': [self.finding], 'assertion_checks': [self.check],
                        'unknowns': ['Independent uncertainty.'], 'comparisons': []}
        self.data = {'protocol_version': 4, 'phase': 'comparison',
            'requirements': [{'id': 'implementation'}, {'id': 'child_objective'}],
            'sources': [{'path': 'example.py', 'version': 'frozen', 'text': 'return values\n'}],
            'advisory': 'Child claim.', 'initial': json.dumps(self.initial)}
        self.delta = {'confirmed_findings': ['implementation'],
            'finding_updates': [{'requirement_id': 'child_objective',
                'judgment': 'Child assessed.', 'citations': [self.cite]}],
            'confirmed_assertions': ['check'], 'assertion_updates': [],
            'unknowns': ['Fresh uncertainty.'],
            'comparisons': [{'child_claim': 'Child claim.', 'paragraph_ids': [1],
                'assessment': 'Qualified.', 'rationale': 'Source behavior.',
                'citations': [self.cite]}]}

    def evaluate(self, value, data=None):
        text = value if isinstance(value, str) else json.dumps(value)
        return evaluate_response(text, ParentReviewSnapshot(data or self.data), comparison=True)

    def test_confirmation_expands_rebinds_and_keeps_explicit_unknowns(self):
        parsed, errors, effective = self.evaluate(self.delta)
        self.assertFalse(errors)
        self.assertEqual(parsed['findings'][0]['judgment'], 'Copies input.')
        self.assertEqual(parsed['assertion_checks'][0]['id'], 'check')
        self.assertEqual(parsed['unknowns'], ['Fresh uncertainty.'])
        self.assertEqual(parsed['findings'][0]['citations'][0]['version'], 'frozen')
        self.assertIn('findings', json.loads(effective))
        self.assertNotIn('confirmed_findings', json.loads(effective))
        self.assertEqual(self.initial['findings'][0]['citations'][0], self.cite)

    def test_complete_updates_and_new_assertions(self):
        value = copy.deepcopy(self.delta)
        value.update(confirmed_findings=[], confirmed_assertions=[])
        value['finding_updates'].append({**self.finding, 'judgment': 'Corrected initial.'})
        value['assertion_updates'] = [{**self.check, 'unknown': 'Corrected uncertainty.'},
                                     {**self.check, 'id': 'new_check'}]
        parsed, errors, _ = self.evaluate(value)
        self.assertFalse(errors)
        self.assertEqual(parsed['findings'][0]['judgment'], 'Corrected initial.')
        self.assertEqual([r['id'] for r in parsed['assertion_checks']], ['check', 'new_check'])

    def test_confirmation_and_update_must_account_each_initial_id_once(self):
        for patch in ({'confirmed_findings': []},
                      {'confirmed_findings': ['implementation', 'implementation']},
                      {'confirmed_assertions': ['missing']},
                      {'assertion_updates': [self.check]},
                      {'finding_updates': [self.finding]}):
            with self.subTest(patch=patch):
                self.assertTrue(self.evaluate({**self.delta, **patch})[1])

    def test_full_validation_rejects_missing_requirement_coverage_and_bad_citation(self):
        for patch in ({'finding_updates': []}, {'comparisons': []},
                      {'assertion_updates': [{**self.check,
                          'citations': [{'path': 'missing.py', 'start_line': 1, 'end_line': 1}]}],
                       'confirmed_assertions': []}):
            with self.subTest(patch=patch):
                parsed, errors, effective = self.evaluate({**self.delta, **patch})
                self.assertTrue(errors)
                self.assertIn('findings', json.loads(effective))

    def test_invalid_initial_and_unknown_top_level_rejected(self):
        bad_data = {**self.data, 'initial': json.dumps({**self.initial, 'findings': []})}
        self.assertTrue(self.evaluate(self.delta, bad_data)[1])
        self.assertTrue(self.evaluate({**self.delta, 'extra': 1})[1])
        self.assertTrue(self.evaluate(self.initial)[1])

    def test_expanded_draft_repairs_only_invalid_fields(self):
        value = copy.deepcopy(self.delta)
        value['comparisons'][0]['citations'][0] = {**self.cite, 'end_line': 99}
        _, errors, effective = self.evaluate(value)
        data = {**self.data, 'repair': list(errors), 'repair_phase': 'comparison',
                'repair_response': effective, 'repair_used': True}
        patch = {'replacements': [{'path': '/comparisons/0/citations/0/end_line', 'value': 1}]}
        parsed, errors, _ = self.evaluate(patch, data)
        self.assertFalse(errors)
        self.assertEqual(parsed['findings'][0]['judgment'], 'Copies input.')
        self.assertTrue(self.evaluate({'replacements': [
            {'path': '/findings/0/judgment', 'value': 'unauthorized'}]}, data)[1])

    def test_invalid_delta_repairs_its_own_missing_confirmation(self):
        value = {**self.delta, 'confirmed_assertions': []}
        _, errors, raw = self.evaluate(value)
        data = {**self.data, 'repair': list(errors), 'repair_phase': 'comparison',
                'repair_response': raw, 'repair_used': True}
        parsed, errors, _ = self.evaluate({'replacements': [
            {'path': '/confirmed_assertions/-', 'value': 'check'}]}, data)
        self.assertFalse(errors)
        self.assertEqual(parsed['assertion_checks'][0]['id'], 'check')

    def test_non_json_comparison_repair_accepts_delta(self):
        _, errors, raw = self.evaluate('{truncated')
        data = {**self.data, 'repair': list(errors), 'repair_phase': 'comparison',
                'repair_response': raw, 'repair_used': True}
        parsed, errors, effective = self.evaluate(self.delta, data)
        self.assertFalse(errors)
        self.assertIn('findings', json.loads(effective))

    def test_stale_delta_repair_empty_confirmation_expands_saved_draft(self):
        data = {**self.data, 'repair': [{'path': '/confirmed_findings/-', 'message': 'old'}],
                'repair_phase': 'comparison', 'repair_response': json.dumps(self.delta)}
        parsed, errors, _ = self.evaluate({'replacements': []}, data)
        self.assertFalse(errors)
        self.assertEqual(parsed['findings'][0]['judgment'], 'Copies input.')
        self.assertFalse(inspect_comparison_draft(self.delta, self.data)[1])

    def test_v3_complete_report_and_independent_behavior_unchanged(self):
        merged, errors = expand_comparison(self.delta, self.data)
        self.assertFalse(errors)
        self.assertFalse(self.evaluate(merged, {**self.data, 'protocol_version': 3})[1])
        self.assertTrue(self.evaluate(self.delta, {**self.data, 'protocol_version': 3})[1])
        data = {**self.data, 'phase': 'independent'}
        parsed, errors, _ = evaluate_response(json.dumps(self.initial),
            ParentReviewSnapshot(data), comparison=False)
        self.assertFalse(errors)


if __name__ == '__main__':
    unittest.main()
