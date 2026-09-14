from __future__ import annotations

from unittest.mock import patch

from _files_test_support import WorkspaceFilesTestCase
from code_agent.workspace.errors import SearchTimeoutError, WorkspaceError
from code_agent.workspace.git import GitTimeoutError


class SearchInventoryTests(WorkspaceFilesTestCase):
    def test_candidates_obey_scope_guard_and_deduplication(self):
        (self.root / 'feature').mkdir()
        (self.root / 'feature/a.py').write_text('needle')
        (self.root / 'other.py').write_text('needle')
        (self.root / '.env').write_text('needle')
        candidates = ('../outside.py', '.env', 'feature/a.py', 'other.py', 'feature/a.py')
        files = self.files()
        def provider(**kwargs):
            self.assertGreater(kwargs['timeout_s'], 0)
            self.assertLessEqual(kwargs['timeout_s'], files.search_timeout_s)
            return candidates
        with patch.object(files, '_iter_files', side_effect=AssertionError('must not walk')):
            matches = files.search('needle', inventory=provider)
        self.assertEqual([m.path for m in matches], ['feature/a.py', 'other.py'])
        matches = files.search('needle', root='feature', inventory=provider)
        self.assertEqual([m.path for m in matches], ['feature/a.py'])
        self.assertEqual(files.search('needle', root='feature', include_globs=('*.txt',), inventory=provider), ())

    def test_inventory_time_consumes_search_deadline(self):
        now = [0.0]
        files = self.files()
        files.search_timeout_s = 2.0
        def provider(**kwargs):
            now[0] = 3.0
            return ('a.py',)
        with patch('code_agent.workspace._text_search.time.monotonic', side_effect=lambda: now[0]):
            with self.assertRaises(SearchTimeoutError):
                files.search('needle', inventory=provider)

    def test_git_timeout_becomes_incomplete_search(self):
        def provider(**kwargs):
            raise GitTimeoutError('snapshot_paths', ('git',), None, b'', b'', kwargs['timeout_s'])
        with self.assertRaises(SearchTimeoutError) as caught:
            self.files().search('needle', inventory=provider)
        self.assertEqual(caught.exception.matches, ())

    def test_inventory_failure_does_not_fall_back(self):
        def provider(**kwargs):
            raise WorkspaceError('inventory failed')
        with self.assertRaisesRegex(WorkspaceError, 'inventory failed'):
            self.files().search('needle', inventory=provider)

    def test_invalid_scope_fails_before_inventory(self):
        for scope in ('../', 'missing'):
            with self.subTest(scope=scope), self.assertRaises(WorkspaceError):
                self.files().search('needle', root=scope, inventory=lambda **kw: self.fail('inventory called'))
