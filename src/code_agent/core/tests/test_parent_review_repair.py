"""Reproduce missing repair feedback and host-owned evidence at the real gate."""
import copy
import json
import unittest

from code_agent.core import parent_review as review


class ParentReviewRepairTests(unittest.TestCase):
    def setUp(self):
        self.data = {'protocol_version': 2, 'phase': 'independent',
            'objective': 'Inspect behavior without executing.', 'child_objective': 'Hidden child objective.',
            'requirements': [{'id': 'implementation', 'evidence_kind': 'source'},
                             {'id': 'task_objective', 'evidence_kind': 'source_or_runtime'}],
            'sources': [{'path': 'example.py', 'version': 'frozen-sha', 'text': 'return values\n'}],
            'runtime_evidence': [{'id': 'parent_review_tool_free', 'description': 'Review has no tools.', 'phase': 'independent'},
                                 {'id': 'child_lifecycle_status', 'description': 'CHILD_LIFECYCLE_SECRET', 'phase': 'comparison'}],
            'advisory': 'CHILD_ADVISORY_SECRET', 'repair_used': False}
        self.output = {'findings': [
            {'requirement_id': 'implementation', 'judgment': 'Returns input unchanged.',
             'citations': [{'path': 'example.py', 'start_line': 1, 'end_line': 1}]},
            {'requirement_id': 'task_objective', 'judgment': 'This review request has no tools.',
             'runtime_refs': ['parent_review_tool_free'], 'citations': []}], 'unknowns': []}

    def test_compact_reference_binds_host_version_and_exact_text(self):
        parsed, missing = review.inspect_delivery(json.dumps(self.output), review.ParentReviewSnapshot(self.data), comparison=False)
        self.assertFalse(missing)
        self.assertEqual(parsed['findings'][0]['citations'][0]['version'], 'frozen-sha')
        self.assertEqual(parsed['findings'][0]['citations'][0]['quote'], 'return values\n')

    def test_repair_receives_same_phase_draft_and_precise_error(self):
        broken = copy.deepcopy(self.output)
        broken['findings'][0]['citations'][0]['end_line'] = 9
        raw = json.dumps(broken)
        _, errors, _ = review.evaluate_response(raw, review.ParentReviewSnapshot(self.data), comparison=False)
        self.assertEqual(errors[0]['path'], '/findings/0/citations/0/end_line')
        self.data.update(repair=errors, repair_response=raw, repair_phase='independent', repair_used=True)
        body = json.loads(review.ParentReviewSnapshot(self.data).bundle().messages[0].content)
        self.assertEqual(body['repair']['previous_response'], raw)
        self.assertEqual(body['repair']['errors'], list(errors))
        self.assertNotIn('CHILD_', json.dumps(body))
        patch = {'replacements': [{'path': errors[0]['path'], 'value': 1}]}
        parsed, errors, effective = review.evaluate_response(json.dumps(patch), review.ParentReviewSnapshot(self.data), comparison=False)
        self.assertFalse(errors)
        self.assertEqual(parsed['findings'][0]['judgment'], broken['findings'][0]['judgment'])
        self.assertEqual(json.loads(effective)['findings'][1], broken['findings'][1])

    def test_repair_cannot_overwrite_correct_fields_or_cross_phases(self):
        raw = json.dumps(self.output)
        self.data.update(repair=[{'path': '/unknowns', 'message': 'explicit unknowns'}],
                         repair_response=raw, repair_phase='independent', repair_used=True)
        patch = {'replacements': [{'path': '/findings/0/judgment', 'value': 'New unreviewed claim'}]}
        self.assertTrue(review.evaluate_response(json.dumps(patch), review.ParentReviewSnapshot(self.data), comparison=False)[1])
        self.data.update(repair_phase='comparison', repair_response='CHILD_ADVISORY_SECRET')
        body = json.loads(review.ParentReviewSnapshot(self.data).bundle().messages[0].content)
        self.assertNotIn('CHILD_', json.dumps(body))

    def test_runtime_cannot_replace_semantics_or_leak_child_facts(self):
        for index, runtime_id in ((0, 'parent_review_tool_free'), (1, 'child_lifecycle_status'), (1, 'invented')):
            candidate = copy.deepcopy(self.output)
            candidate['findings'][index].update(citations=[], runtime_refs=[runtime_id])
            self.assertTrue(review.inspect_delivery(json.dumps(candidate), review.ParentReviewSnapshot(self.data), comparison=False)[1])

    def test_explicit_bad_metadata_is_not_silently_replaced(self):
        for field, value in (('version', 'invented'), ('quote', 'invented'), ('start_line', True)):
            candidate = copy.deepcopy(self.output)
            candidate['findings'][0]['citations'][0][field] = value
            self.assertTrue(review.inspect_delivery(json.dumps(candidate), review.ParentReviewSnapshot(self.data), comparison=False)[1])

    def test_removing_invalid_array_items_cannot_remove_valid_siblings(self):
        bad = copy.deepcopy(self.output)
        bad['findings'] += [None, {'requirement_id': 'unknown'}]
        raw = json.dumps(bad)
        _, errors, _ = review.evaluate_response(raw, review.ParentReviewSnapshot(self.data), comparison=False)
        self.data.update(repair=list(errors), repair_response=raw, repair_phase='independent')
        patch = {'replacements': [{'path': '/findings/2', 'remove': True}, {'path': '/findings/3', 'remove': True}]}
        parsed, errors, effective = review.evaluate_response(json.dumps(patch), review.ParentReviewSnapshot(self.data), comparison=False)
        self.assertFalse(errors)
        self.assertEqual(json.loads(effective), self.output)
        for forbidden in ([None], [{'path': [], 'value': 1}], [{'path': '/findings/0', 'remove': True}],
                          [{'path': '', 'value': self.output}], [{'path': 'x' * 1000, 'value': 1}]):
            errors = review.evaluate_response(json.dumps({'replacements': forbidden}),
                review.ParentReviewSnapshot(self.data), comparison=False)[1]
            self.assertTrue(errors)
            self.assertTrue(all(len(error['path']) <= 512 for error in errors))

    def test_deep_or_non_finite_json_is_a_durable_validation_error(self):
        for raw in ('{"extra":' + '[' * 100 + '0' + ']' * 100 + '}', '{"extra":NaN}', '{"extra":1e999}'):
            self.assertTrue(review.evaluate_response(raw, review.ParentReviewSnapshot(self.data), comparison=False)[1])

    def test_process_fact_can_be_repaired_without_fake_source_quote(self):
        bad = copy.deepcopy(self.output)
        bad['findings'][1].pop('runtime_refs')
        raw = json.dumps(bad)
        _, errors, _ = review.evaluate_response(raw, review.ParentReviewSnapshot(self.data), comparison=False)
        self.data.update(repair=list(errors), repair_response=raw, repair_phase='independent')
        patch = {'replacements': [{'path': '/findings/1/runtime_refs', 'value': ['parent_review_tool_free']}]}
        parsed, errors, _ = review.evaluate_response(json.dumps(patch), review.ParentReviewSnapshot(self.data), comparison=False)
        self.assertFalse(errors)
        self.assertEqual(parsed['findings'][1]['citations'], [])
        self.assertEqual(parsed['findings'][1]['runtime_evidence'][0]['id'], 'parent_review_tool_free')

    def test_legacy_snapshot_accepts_complete_repair_with_strict_citations(self):
        self.data.pop('protocol_version')
        self.data.update(repair=['structured review findings'], rejected_initial='Only a plan.')
        for finding in self.output['findings']:
            finding.pop('runtime_refs', None)
            finding['citations'] = [{'path': 'example.py', 'version': 'frozen-sha',
                                     'start_line': 1, 'end_line': 1, 'quote': 'return values'}]
        self.assertFalse(review.evaluate_response(json.dumps(self.output),
            review.ParentReviewSnapshot(self.data), comparison=False)[1])
        self.output['findings'][0]['citations'][0].pop('version')
        self.assertTrue(review.evaluate_response(json.dumps(self.output),
            review.ParentReviewSnapshot(self.data), comparison=False)[1])


if __name__ == '__main__':
    unittest.main()
