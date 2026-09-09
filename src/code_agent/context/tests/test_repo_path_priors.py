from __future__ import annotations

import unittest

from code_agent.context.models import RepoEntry
from code_agent.context.repo_ranking import _lexical_path_prior, rank_repo_entries
from code_agent.context.repo_search import RepoLexicalRanks


class RepoPathPriorTests(unittest.TestCase):
    def test_default_priors_include_non_source_files(self):
        for path, expected in (
            ("app.py", 1.0), ("pyproject.toml", 1.0), ("data.json", 1.0),
            ("tests/test_app.py", 0.25), ("test_app.py", 0.25),
            ("docs/design.txt", 0.30), ("README.md", 0.30),
            ("AGENTS.md", 0.30), ("src/AGENTS.md", 0.30),
        ):
            with self.subTest(path=path):
                self.assertEqual(_lexical_path_prior(path), expected)

    def test_test_keywords_restore_only_test_prior(self):
        for query in ("test", "TEST failure", "pytest", "unittest", "测试失败", "修复测试"):
            with self.subTest(query=query):
                self.assertEqual(_lexical_path_prior("tests/check.py", query), 1.0)
                self.assertEqual(_lexical_path_prior("docs/guide.md", query), 0.30)

    def test_docs_keywords_restore_only_docs_prior(self):
        for query in ("docs", "README", "documentation", "文档", "设计文档"):
            with self.subTest(query=query):
                self.assertEqual(_lexical_path_prior("docs/guide.md", query), 1.0)
                self.assertEqual(_lexical_path_prior("tests/check.py", query), 0.25)

    def test_keyword_substrings_do_not_restore_priors(self):
        for query in ("latest contest", "documentary", "doctest_helper", "app behavior"):
            self.assertEqual(_lexical_path_prior("tests/check.py", query), 0.25)
            self.assertEqual(_lexical_path_prior("docs/guide.md", query), 0.30)

    def test_explicit_path_restores_prior_and_beats_touched_and_lexical_scores(self):
        for path, query in (
            ("tests/check.py", "inspect `tests/check.py:12`"),
            ("docs/design.md", r"inspect DOCS\DESIGN.MD"),
            ("README.md", "inspect README.md"),
        ):
            with self.subTest(path=path):
                self.assertEqual(_lexical_path_prior(path, query), 1.0)
                entries = (RepoEntry("app.py"), RepoEntry(path))
                lexical = RepoLexicalRanks(
                    term_paths=("app.py",), trigram_paths=("app.py",),
                    short_paths=("app.py",), contract_paths=("app.py",),
                )
                ranked = rank_repo_entries(entries, query, ("app.py",), lexical)
                self.assertEqual(ranked[0].path, path)

    def test_longer_filename_is_not_an_explicit_path_match(self):
        target = "tests/check.py"
        entries = (RepoEntry("app.py"), RepoEntry(target))
        for query in ("inspect tests/check.py.bak", "inspect mytests/check.py"):
            ranked = rank_repo_entries(entries, query, ("app.py",))
            self.assertEqual(ranked[0].path, "app.py")

    def test_query_restoration_changes_lexical_ranking(self):
        for path, query in (("tests/check.py", "测试失败"), ("docs/guide.md", "设计文档")):
            entries = (RepoEntry("app.py"), RepoEntry(path))
            lexical = RepoLexicalRanks(term_paths=(path, "app.py"))
            self.assertEqual(rank_repo_entries(entries, "behavior", (), lexical)[0].path, "app.py")
            ranked = rank_repo_entries(entries, query, (), lexical)
            self.assertEqual(ranked[0].path, path)
            self.assertEqual(ranked, rank_repo_entries(entries, query, (), lexical))

    def test_query_bound_and_mixed_categories(self):
        query = "test docs"
        self.assertEqual(_lexical_path_prior("tests/check.py", query), 1.0)
        self.assertEqual(_lexical_path_prior("docs/guide.md", query), 1.0)
        query = "x" * 512 + " tests/check.py pytest docs"
        self.assertEqual(_lexical_path_prior("tests/check.py", query), 0.25)
        self.assertEqual(_lexical_path_prior("docs/guide.md", query), 0.30)
