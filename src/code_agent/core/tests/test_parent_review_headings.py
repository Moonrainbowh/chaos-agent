"""Advisory headings remain visible without requiring standalone judgments."""
import copy
import unittest

from code_agent.core import _parent_review_validation as validation


class ParentReviewHeadingsTests(unittest.TestCase):
    def setUp(self):
        self.source = {'path': 'example.py', 'version': 'frozen', 'text': 'return value\n'}

    def inspect(self, advisory, ids, *, version=2):
        citation = {'path': 'example.py', 'version': 'frozen', 'start_line': 1,
                    'end_line': 1, 'quote': 'return value\n'}
        candidate = {'findings': [], 'unknowns': [], 'comparisons': [
            {'child_claim': 'The child claim.', 'assessment': 'Agreed.',
             'rationale': 'Source supports the claim.', 'paragraph_ids': ids,
             'citations': [citation]}]}
        data = {'protocol_version': version, 'requirements': [], 'runtime_evidence': [],
                'sources': [self.source], 'advisory': advisory}
        return validation.inspect_review(copy.deepcopy(candidate), data, comparison=True)[1]

    def grouped(self, text, *, compact=True):
        return validation.comparison_paragraphs(text, compact=compact)

    def test_real_failure_heading_ids_no_longer_require_separate_comparisons(self):
        blocks = [f'Substantive paragraph {i}.' for i in range(1, 15)]
        blocks[4], blocks[8], blocks[11] = (
            '### 代码 vs 契约', '### 测试 vs 契约', '### 覆盖缺口（审查要点）')
        self.assertFalse(self.inspect('\n\n'.join(blocks),
            [i for i in range(1, 15) if i not in (5, 9, 12)]))

    def test_headings_and_factual_title_remain_in_next_body(self):
        advisory = '# Report\n\n## All five tests pass\n\nFirst body.\n\nSecond body.'
        self.assertEqual(self.grouped(advisory), [
            {'id': 3, 'text': '# Report\n\n## All five tests pass\n\nFirst body.'},
            {'id': 4, 'text': 'Second body.'}])
        self.assertFalse(self.inspect(advisory, [3, 4]))
        self.assertTrue(self.inspect(advisory, [1, 2, 4]))

    def test_missing_substantive_body_is_still_rejected(self):
        errors = self.inspect('# Report\n\nFirst claim.\n\nSecond claim.', [2])
        self.assertIn('uncovered paragraphs: [3]', errors[-1]['message'])

    def test_extra_old_heading_ids_are_compatible_but_do_not_cover_body(self):
        self.assertFalse(self.inspect('# Report\n\nBody.', [1, 2]))
        self.assertTrue(self.inspect('# Report\n\nBody.', [1]))
        self.assertTrue(self.inspect('# Report\n\nBody.', [2, 99]))

    def test_trailing_headings_are_retained_with_last_body(self):
        self.assertEqual(self.grouped('Body.\n\n## Tail\n\n### Factual assertion'),
            [{'id': 1, 'text': 'Body.\n\n## Tail\n\n### Factual assertion'}])
        self.assertFalse(self.inspect('Body.\n\n## Tail', [1]))

    def test_heading_only_report_requires_nonempty_complete_coverage(self):
        advisory = '# First\n\n## Second'
        self.assertEqual(self.grouped(advisory), validation.paragraphs(advisory))
        self.assertTrue(self.inspect(advisory, []))
        self.assertTrue(self.inspect(advisory, [1]))
        self.assertFalse(self.inspect(advisory, [1, 2]))

    def test_v1_retains_original_paragraphs_and_strict_heading_coverage(self):
        advisory = '# Heading\n\nBody.'
        self.assertEqual(self.grouped(advisory, compact=False), validation.paragraphs(advisory))
        self.assertTrue(self.inspect(advisory, [2], version=1))
        self.assertFalse(self.inspect(advisory, [1, 2], version=1))

    def test_crlf_is_equivalent_in_compact_groups(self):
        advisory = '# Heading\n\nBody.\n\n## Tail'
        self.assertEqual(self.grouped(advisory.replace('\n', '\r\n')), self.grouped(advisory))
        self.assertFalse(self.inspect(advisory.replace('\n', '\r\n'), [2]))

    def test_fenced_hash_lines_are_substantive_even_across_blank_lines(self):
        for fence in ('```', '~~~~'):
            advisory = f'{fence}python\n\n# comment\n\n{fence}\n\nBody.'
            with self.subTest(fence=fence):
                self.assertEqual(self.grouped(advisory), validation.paragraphs(advisory))
                self.assertTrue(self.inspect(advisory, [1, 3, 4]))

    def test_fence_with_wrong_close_length_does_not_expose_heading(self):
        advisory = '````python\n\n```\n\n# comment\n\n````\n\nBody.'
        self.assertEqual(self.grouped(advisory), validation.paragraphs(advisory))
        self.assertTrue(self.inspect(advisory, [1, 2, 4, 5]))

    def test_fence_closure_restores_heading_grouping(self):
        advisory = '```python\n# comment\n```\n\n## Outside\n\nBody.'
        self.assertEqual(self.grouped(advisory), [
            {'id': 1, 'text': '```python\n# comment\n```'},
            {'id': 3, 'text': '## Outside\n\nBody.'}])
        self.assertFalse(self.inspect(advisory, [1, 3]))

    def test_heading_between_bodies_attaches_only_to_following_body(self):
        advisory = 'Before.\n\n# Assertion\n\nAfter.'
        self.assertEqual(self.grouped(advisory), [
            {'id': 1, 'text': 'Before.'}, {'id': 3, 'text': '# Assertion\n\nAfter.'}])
        self.assertTrue(self.inspect(advisory, [1, 2]))
        self.assertFalse(self.inspect(advisory, [1, 3]))

    def test_atx_syntax_and_mixed_paragraphs(self):
        for block in ('#hashtag', '####### not ATX', '    # indented code',
                      '# Heading\nBody.', '> # quoted heading'):
            with self.subTest(block=block):
                advisory = block + '\n\nBody.'
                self.assertEqual(self.grouped(advisory), validation.paragraphs(advisory))
                self.assertTrue(self.inspect(advisory, [2]))
        self.assertEqual(self.grouped('  ## Header ##\n### More\n\nBody.'),
            [{'id': 2, 'text': '## Header ##\n### More\n\nBody.'}])


if __name__ == '__main__':
    unittest.main()
