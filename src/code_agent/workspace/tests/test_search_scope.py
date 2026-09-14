from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from _files_test_support import WorkspaceFilesTestCase
from code_agent.workspace._text_search import search_text
from code_agent.workspace.errors import SearchTimeoutError, WorkspaceError


class SearchScopeTests(WorkspaceFilesTestCase):
    def test_scope_prunes_enumeration_and_retains_workspace_paths(self):
        (self.root / 'feature').mkdir()
        (self.root / 'elsewhere').mkdir()
        (self.root / 'feature/a.py').write_text('needle')
        (self.root / 'feature/a.txt').write_text('needle')
        (self.root / 'elsewhere/b.py').write_text('needle')
        visited = []
        original = os.scandir
        def scandir(path):
            visited.append(Path(path))
            return original(path)
        with patch('code_agent.workspace._file_walk.os.scandir', side_effect=scandir):
            matches = self.files().search('needle', root='feature', include_globs=('*.py',))
        self.assertEqual([m.path for m in matches], ['feature/a.py'])
        self.assertEqual(visited, [self.root / 'feature'])
        self.assertEqual(self.files().search('needle', root='feature/a.txt')[0].path, 'feature/a.txt')

    def test_scoped_search_obeys_ignore_and_containment(self):
        (self.root / '.gitignore').write_text('secret.txt\n')
        (self.root / 'secret.txt').write_text('needle')
        self.assertEqual(self.files().search('needle', root='secret.txt'), ())
        for root in ('../', 'missing'):
            with self.subTest(root=root), self.assertRaises(WorkspaceError):
                self.files().search('needle', root=root)

    def test_deadline_preserves_matches_during_next_directory_scan(self):
        now = [0.0]
        def files(limit, check):
            yield 'a.py'
            now[0] = 3.0
            check()
            yield 'b.py'
        with patch('code_agent.workspace._text_search.time.monotonic', side_effect=lambda: now[0]):
            with self.assertRaises(SearchTimeoutError) as caught:
                search_text(files, lambda p: SimpleNamespace(text='needle'), 2, 'needle')
        self.assertEqual([m.path for m in caught.exception.matches], ['a.py'])

    def test_deadline_with_no_matches_is_still_incomplete(self):
        def files(limit, check):
            raise SearchTimeoutError('scan deadline')
            yield
        with self.assertRaises(SearchTimeoutError) as caught:
            search_text(files, lambda p: None, 2, 'needle')
        self.assertEqual(caught.exception.matches, ())
