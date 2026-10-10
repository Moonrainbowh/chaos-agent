"""Adversarial model output at the parent delivery boundary."""
import copy
import asyncio
import json
import unittest
from types import SimpleNamespace

from code_agent.core.parent_review import (
    ParentReviewSnapshot, inspect_delivery, render_delivery,
)
from code_agent.core._engine_parent_review import AgentEngineParentReviewMixin


class ParentReviewContractTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = ParentReviewSnapshot({
            'objective': 'Review the supplied source.', 'child_objective': 'Inspect it.',
            'phase': 'comparison', 'requirements': [{'id': 'behavior'}],
            'sources': [{'path': 'sample.py', 'version': 'frozen-version',
                         'text': 'first line\nsecond line\n'}],
            'advisory': 'First assertion.\n\nA separate additional assertion.',
            'initial': 'Independent result.',
        })
        self.citation = {'path': 'sample.py', 'version': 'frozen-version',
                         'start_line': 2, 'end_line': 2, 'quote': 'second line'}
        self.delivery = {
            'findings': [{'requirement_id': 'behavior', 'judgment': 'Scoped finding.',
                          'citations': [self.citation]}],
            'comparisons': [{'child_claim': 'Both assertions are compared.',
                             'paragraph_ids': [1, 2], 'assessment': 'Qualified.',
                             'rationale': 'Static analysis of the source.',
                             'citations': [self.citation]}],
            'unknowns': [],
        }

    def inspect(self, value):
        return inspect_delivery(json.dumps(value), self.snapshot, comparison=True)

    def test_complete_delivery_renders_without_upgrading_verification(self):
        parsed, missing = self.inspect(self.delivery)
        self.assertEqual(missing, ())
        rendered = render_delivery(parsed)
        self.assertIn('sample.py:2', rendered)
        self.assertIn('未经过运行验证', rendered)
        self.assertNotIn('"findings"', rendered)

    def test_single_complete_fence_only(self):
        raw = json.dumps(self.delivery)
        fenced = '```json\n' + raw + '\n```'
        self.assertFalse(inspect_delivery(fenced, self.snapshot, comparison=True)[1])
        for invalid in (fenced + '\nI will continue.', 'preface\n' + fenced,
                        raw[:-1], '```json\n' + raw, '[]'):
            with self.subTest(invalid=invalid[:30]):
                self.assertTrue(inspect_delivery(invalid, self.snapshot, comparison=True)[1])

    def test_source_reference_must_match_real_version_lines_and_quote(self):
        for key, invalid in (('path', 'other.py'), ('path', {}),
                             ('version', 'stale'), ('version', []),
                             ('start_line', True), ('end_line', 3),
                             ('quote', 'first line'), ('quote', {})):
            with self.subTest(key=key, invalid=invalid):
                candidate = copy.deepcopy(self.delivery)
                candidate['findings'][0]['citations'][0][key] = invalid
                self.assertTrue(self.inspect(candidate)[1])

    def test_malformed_surplus_findings_cannot_crash_or_bypass_gate(self):
        for extra in (None, [], {}, {'requirement_id': []},
                      {'requirement_id': 'undeclared'}, self.delivery['findings'][0]):
            with self.subTest(extra=extra):
                candidate = copy.deepcopy(self.delivery)
                candidate['findings'].append(extra)
                self.assertTrue(self.inspect(candidate)[1])

    def test_each_advisory_paragraph_and_unknowns_are_required(self):
        for paragraph_ids in ([1], [2], [3], [True], [[]], []):
            with self.subTest(paragraph_ids=paragraph_ids):
                candidate = copy.deepcopy(self.delivery)
                candidate['comparisons'][0]['paragraph_ids'] = paragraph_ids
                self.assertTrue(self.inspect(candidate)[1])
        candidate = copy.deepcopy(self.delivery)
        candidate.pop('unknowns')
        self.assertTrue(any('explicit unknowns' in error for error in self.inspect(candidate)[1]))

    def test_empty_source_has_explicit_empty_reference(self):
        self.snapshot.data['sources'][0]['text'] = ''
        candidate = copy.deepcopy(self.delivery)
        for item in (*candidate['findings'], *candidate['comparisons']):
            item['citations'][0].update(start_line=0, end_line=0, quote='')
        self.assertEqual(self.inspect(candidate)[1], ())

    def test_independent_bundle_omits_advisory_and_rejected_history(self):
        self.snapshot.data.update(phase='independent',
                                  child_objective='IMPORTED_CHILD_CONCLUSION',
                                  rejected_final='OLD_CHILD_ASSERTION',
                                  rendered='OLD_FINAL_ANSWER')
        body = json.loads(self.snapshot.bundle().messages[0].content)
        self.assertEqual(body['sources'], self.snapshot.data['sources'])
        self.assertNotIn('child_advisory', body)
        self.assertNotIn('initial', body)
        self.assertNotIn('IMPORTED_CHILD_CONCLUSION', json.dumps(body))
        self.assertNotIn('OLD_CHILD_ASSERTION', json.dumps(body))
        self.assertNotIn('OLD_FINAL_ANSWER', json.dumps(body))

    def test_budget_pause_after_delivery_reports_finalization_remaining(self):
        engine = AgentEngineParentReviewMixin()
        engine._parent_review = None
        state = SimpleNamespace(parent_review=ParentReviewSnapshot({'phase': 'delivered'}))
        result = asyncio.run(engine._parent_review_pause_result(state, 'budget exhausted'))
        self.assertEqual(result['execution_status'], 'paused')
        self.assertEqual(result['verification_status'], 'unverified')
        self.assertEqual(result['remaining'], ['task finalization after delivered parent source review'])


if __name__ == '__main__':
    unittest.main()
