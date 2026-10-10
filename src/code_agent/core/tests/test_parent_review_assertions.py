"""Literal witness consistency checks do not execute or prove source behavior."""
import copy
import json
import math
import unittest
from unittest.mock import patch

from code_agent.core._parent_review_assertions import inspect_assertions


class ParentReviewAssertionTests(unittest.TestCase):
    def setUp(self):
        self.data = {'sources': [{'path': 'example.py', 'version': 'fixed',
                                 'text': 'assert output == expected\n'}]}
        self.record = {
            'id': 'example',
            'citations': [{'path': 'example.py', 'start_line': 1, 'end_line': 1}],
            'input': [' Z ', 'Z'], 'observed': 'return value',
            'expected': ['Z', 'Z'],
            'current': {'actual': [' Z ', 'Z'], 'trace': 'Copy each input element.'},
            'detected': {'actual': ['Z'], 'fault': 'Removes equal cleaned values.'},
            'missed': {'actual': ['Z', 'Z'], 'fault': 'Sorts cleaned values.',
                       'counterexample': {'input': ['Z', 'B'],
                                          'expected': ['Z', 'B'], 'actual': ['B', 'Z']}},
            'unknowns': [],
        }

    def inspect(self, record=None, *, unknowns=None):
        parsed = {'assertion_checks': [copy.deepcopy(record or self.record)],
                  'unknowns': [] if unknowns is None else unknowns}
        errors = []
        inspect_assertions(parsed, self.data, errors)
        return parsed, errors

    def test_valid_witness_is_host_bound_and_outcome_recomputed(self):
        candidate = copy.deepcopy(self.record)
        candidate['current_outcome'] = 'pass'
        parsed, errors = self.inspect(candidate)
        self.assertEqual(errors, [])
        bound = parsed['assertion_checks'][0]
        self.assertEqual(bound['current_outcome'], 'fail')
        self.assertEqual(bound['scope'], 'witness_only')
        self.assertEqual(bound['citations'][0]['version'], 'fixed')
        self.assertEqual(bound['citations'][0]['quote'], self.data['sources'][0]['text'])
        candidate['current']['actual'] = ['Z', 'Z']
        candidate['current_outcome'] = 'fail'
        self.assertEqual(self.inspect(candidate)[0]['assertion_checks'][0]['current_outcome'], 'pass')
        repeat_errors = []
        inspect_assertions(parsed, self.data, repeat_errors)
        self.assertEqual(repeat_errors, [])
        self.assertEqual(parsed['assertion_checks'][0]['current_outcome'], 'fail')

    def test_empty_assertion_has_both_rejected_and_missed_faults(self):
        candidate = copy.deepcopy(self.record)
        candidate.update(input=[], expected=[], current={'actual': [], 'trace': 'Copy an empty list.'},
                         detected={'actual': ['END'], 'fault': 'Appends a sentinel.'},
                         missed={'actual': [], 'fault': 'Always returns an empty list.',
                                 'counterexample': {'input': ['Q'], 'expected': ['Q'], 'actual': []}})
        parsed, errors = self.inspect(candidate)
        self.assertEqual(errors, [])
        self.assertEqual(parsed['assertion_checks'][0]['current_outcome'], 'pass')

    def test_reject_false_detected_witness_for_duplicates_order_and_empty(self):
        for expected in (['Y', 'Y'], ['Y', 'B'], []):
            with self.subTest(expected=expected):
                candidate = copy.deepcopy(self.record)
                candidate['expected'] = expected
                candidate['detected']['actual'] = expected
                _, errors = self.inspect(candidate)
                self.assertIn('/assertion_checks/0/detected', [e['path'] for e in errors])

    def test_reject_false_missed_witness_for_duplicates_order_and_empty(self):
        for expected, actual in ((['Y', 'Y'], [' Y ', 'Y']),
                                 (['Y', 'B'], ['B', 'Y']), ([], ['X'])):
            with self.subTest(expected=expected):
                candidate = copy.deepcopy(self.record)
                candidate['expected'] = expected
                candidate['missed']['actual'] = actual
                _, errors = self.inspect(candidate)
                self.assertIn('/assertion_checks/0/missed', [e['path'] for e in errors])

    def test_boolean_is_not_numeric_but_integer_and_float_compare_equal(self):
        for actual, expected, outcome in ((False, 0, 'fail'), (True, 1, 'fail'),
                                          (1, 1.0, 'pass'), (0, 0.0, 'pass')):
            with self.subTest(actual=actual, expected=expected):
                candidate = copy.deepcopy(self.record)
                candidate.update(expected=expected, detected=None, missed=None, unknowns=['No witnesses.'])
                candidate['current']['actual'] = actual
                parsed, errors = self.inspect(candidate)
                self.assertEqual(errors, [])
                self.assertEqual(parsed['assertion_checks'][0]['current_outcome'], outcome)

    def test_nested_object_keys_and_array_order_are_checked(self):
        candidate = copy.deepcopy(self.record)
        candidate.update(expected={'a': [1, False], 'b': None},
                         detected=None, missed=None, unknowns=['Other faults not established.'])
        candidate['current']['actual'] = {'b': None, 'a': [1.0, False]}
        self.assertEqual(self.inspect(candidate)[0]['assertion_checks'][0]['current_outcome'], 'pass')
        candidate['current']['actual']['a'] = [False, 1]
        self.assertEqual(self.inspect(candidate)[0]['assertion_checks'][0]['current_outcome'], 'fail')

    def test_every_required_field_and_citation_is_checked(self):
        for field in self.record:
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.record)
                del candidate[field]
                self.assertTrue(self.inspect(candidate)[1])
        for citations in ([], [{'path': 'absent', 'start_line': 1, 'end_line': 1}]):
            candidate = copy.deepcopy(self.record)
            candidate['citations'] = citations
            self.assertTrue(self.inspect(candidate)[1])

    def test_null_witnesses_require_explicit_unknowns(self):
        for field in ('detected', 'missed'):
            candidate = copy.deepcopy(self.record)
            candidate[field] = None
            self.assertTrue(self.inspect(candidate)[1])
            candidate['unknowns'] = ['Could not establish this witness.']
            self.assertFalse(self.inspect(candidate)[1])

    def test_counterexample_must_violate_expected_result(self):
        candidate = copy.deepcopy(self.record)
        candidate['missed']['counterexample']['actual'] = ['Z', 'B']
        _, errors = self.inspect(candidate)
        self.assertIn('/assertion_checks/0/missed', [e['path'] for e in errors])
        for field in ('input', 'expected', 'actual'):
            candidate = copy.deepcopy(self.record)
            del candidate['missed']['counterexample'][field]
            self.assertTrue(self.inspect(candidate)[1])

    def test_scope_and_ids_do_not_allow_universal_or_duplicate_claims(self):
        for scope in ('all', 'none', None):
            candidate = copy.deepcopy(self.record)
            candidate['scope'] = scope
            self.assertTrue(self.inspect(candidate)[1])
        parsed = {'assertion_checks': [self.record, copy.deepcopy(self.record)], 'unknowns': []}
        errors = []
        inspect_assertions(parsed, self.data, errors)
        self.assertTrue(errors)

    def test_unknown_field_pointer_can_remove_slash_and_tilde_without_other_changes(self):
        from code_agent.core.parent_review import ParentReviewSnapshot
        from code_agent.core._parent_review_repair import evaluate_response

        for key, escaped in (('a/b', 'a~1b'), ('a~b', 'a~0b'), ('a~/b', 'a~0~1b')):
            with self.subTest(key=key):
                data = {**self.data, 'protocol_version': 3, 'phase': 'independent',
                        'requirements': [{'id': 'implementation', 'evidence_kind': 'source'}]}
                original = {'findings': [{'requirement_id': 'implementation',
                                         'judgment': 'Static review only.',
                                         'citations': copy.deepcopy(self.record['citations'])}],
                            'assertion_checks': [copy.deepcopy(self.record)], 'unknowns': []}
                original['assertion_checks'][0][key] = 'Unexpected field.'
                raw = json.dumps(original)
                _, errors, _ = evaluate_response(raw, ParentReviewSnapshot(data), comparison=False)
                pointer = '/assertion_checks/0/' + escaped
                self.assertEqual([error['path'] for error in errors], [pointer])
                data.update(repair=list(errors), repair_response=raw, repair_phase='independent')
                patch_body = {'replacements': [{'path': pointer, 'remove': True}]}
                parsed, errors, effective = evaluate_response(
                    json.dumps(patch_body), ParentReviewSnapshot(data), comparison=False)
                self.assertFalse(errors)
                self.assertNotIn(key, parsed['assertion_checks'][0])
                expected = copy.deepcopy(original)
                del expected['assertion_checks'][0][key]
                self.assertEqual(json.loads(effective), expected)

    def test_unsupported_and_empty_table_preserve_unknown(self):
        unsupported = {'id': 'opaque', 'citations': self.record['citations'],
                       'unknown': 'Custom equality cannot be compared as ordinary JSON.'}
        self.assertFalse(self.inspect(unsupported)[1])
        unsupported['expected'] = []
        self.assertTrue(self.inspect(unsupported)[1])
        for unknowns, valid in (([], False), (['No comparable assertion exists.'], True), ([''], False)):
            parsed = {'assertion_checks': [], 'unknowns': unknowns}
            errors = []
            inspect_assertions(parsed, self.data, errors)
            self.assertEqual(not errors, valid)

    def test_invalid_json_values_and_capacity_are_bounded(self):
        nested = []
        for _ in range(34):
            nested = [nested]
        for value in (math.nan, math.inf, {'bad': math.inf}, (1, 2), nested, {1: 'bad'}):
            candidate = copy.deepcopy(self.record)
            candidate['input'] = value
            self.assertTrue(self.inspect(candidate)[1])
        for table in (None, {}, [self.record] * 65):
            parsed = {'assertion_checks': table, 'unknowns': []}
            errors = []
            inspect_assertions(parsed, self.data, errors)
            self.assertTrue(errors)

    def test_nested_shape_errors_and_cycles_are_rejected_without_comparison(self):
        cycle = []
        cycle.append(cycle)
        for value in (math.nan, cycle):
            for field in ('current', 'detected', 'missed'):
                candidate = copy.deepcopy(self.record)
                candidate[field]['actual'] = value
                self.assertTrue(self.inspect(candidate)[1])
        for field, required in (('current', ('actual', 'trace')),
                                ('detected', ('actual', 'fault')),
                                ('missed', ('actual', 'fault', 'counterexample'))):
            for child in required:
                candidate = copy.deepcopy(self.record)
                del candidate[field][child]
                self.assertTrue(self.inspect(candidate)[1])
        candidate = copy.deepcopy(self.record)
        candidate['id'] = []
        self.assertTrue(self.inspect(candidate)[1])
        candidate['unknowns'] = ['']
        self.assertTrue(self.inspect(candidate)[1])

    def test_source_and_model_text_are_never_executed(self):
        # Load the Python validator module before intercepting dynamic execution;
        # ordinary import machinery is outside the model/source execution claim.
        from code_agent.core import _parent_review_validation  # noqa: F401
        candidate = copy.deepcopy(self.record)
        candidate['current']['trace'] = "raise RuntimeError('must remain text')"
        candidate['detected']['fault'] = "__import__('os').system('should never run')"
        with patch('builtins.eval', side_effect=AssertionError('eval called')), \
                patch('builtins.exec', side_effect=AssertionError('exec called')):
            self.assertFalse(self.inspect(candidate)[1])


if __name__ == '__main__':
    unittest.main()
