from __future__ import annotations

import unittest

from code_agent.context.models import RepoEntry, Symbol
from code_agent.context.repo_index import RepoIndexSnapshot
from code_agent.context.repo_tiered_context import select_tiered_context


class AnchorProseTests(unittest.TestCase):
    def entries(self):
        def entry(path, name, kind, doc=''):
            return RepoEntry(path, (Symbol(path, name, kind, 1, 8, '', doc),), (), 100)
        return (
            entry('feature/cache.py', 'evict_records', 'function', 'Session cache expiry'),
            entry('unrelated/session.py', 'Session', 'class'),
        )

    def select(self, query, ranked=None):
        entries = self.entries()
        return select_tiered_context(RepoIndexSnapshot(1, entries), entries if ranked is None else ranked, query, (), 4000)

    def test_prose_name_does_not_override_relevant_file(self):
        result = self.select('Repair Session cache expiry')
        self.assertEqual(result.l0[0].path, 'feature/cache.py')

    def test_explicit_code_reference_can_locate_outside_candidates(self):
        for query in ('Repair `Session`', 'Repair Session()', 'Session'):
            with self.subTest(query=query):
                result = self.select(query, ranked=self.entries()[:1])
                self.assertEqual(result.l0[0].path, 'unrelated/session.py')

    def test_named_file_still_locates_its_symbol(self):
        self.assertEqual(self.select('Repair Session in unrelated/session.py').l0[0].path, 'unrelated/session.py')

    def test_compound_function_name_still_locates_outside_candidates(self):
        self.assertEqual(self.select('Repair evict_records', ranked=self.entries()[1:]).l0[0].symbol, 'evict_records')
