from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.models import (  # noqa: E402
    ContextConfig,
    RepoEntry,
    Symbol,
)
from code_agent.context.repo_map import (  # noqa: E402
    RepoMapBuilder,
    RepoMapViewCache,
)
from code_agent.context.repo_ranking import rank_repo_entries  # noqa: E402
from code_agent.context.repo_search import RepoLexicalRanks  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class RepoRankingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.files = self._files(self.root)
        self.config = ContextConfig(
            self.root,
            self.root,
            "System",
            repo_scan=100,
            repo_map_tokens=200,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _files(root: Path) -> WorkspaceFiles:
        guard = WorkspacePathGuard(root)
        return WorkspaceFiles(guard, IgnoreRules.from_workspace(root))

    def write(self, relative: str, content: str) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def test_rrf_combines_lexical_symbol_and_graph_channels(self) -> None:
        entries = (
            RepoEntry("docs.py"),
            RepoEntry(
                "dispatcher.py",
                (Symbol("dispatcher.py", "authorize_action", "function", 1),),
            ),
            RepoEntry("policy.py"),
        )
        lexical = RepoLexicalRanks(
            term_paths=("policy.py", "docs.py", "dispatcher.py"),
            trigram_paths=("policy.py",),
            backend_key="fts5+trigram",
        )

        ranked = rank_repo_entries(
            entries,
            "authorize_action",
            (),
            lexical,
        )

        self.assertEqual(ranked[0].path, "dispatcher.py")
        self.assertEqual(len({entry.path for entry in ranked}), 3)

    def test_only_strongest_contract_expands_its_feature_scope(self) -> None:
        entries = (
            RepoEntry("policy/AGENTS.md"),
            RepoEntry("policy/engine.py"),
            RepoEntry("runtime/AGENTS.md"),
            RepoEntry("runtime/validation.py"),
        )
        lexical = RepoLexicalRanks(
            contract_paths=("policy/AGENTS.md", "runtime/AGENTS.md"),
        )

        ranked = rank_repo_entries(entries, "权限校验", (), lexical)
        paths = tuple(item.path for item in ranked)

        self.assertLess(
            paths.index("policy/engine.py"),
            paths.index("runtime/validation.py"),
        )

    def test_lexical_test_fixture_does_not_hide_production_source(self) -> None:
        self.write(
            "policy.py",
            "# permission approval policy\n"
            "def authorize_action():\n"
            "    pass\n",
        )
        self.write(
            "tests/test_policy.py",
            "# permission approval policy permission approval policy\n"
            "def test_authorize_action():\n"
            "    pass\n",
        )
        builder = RepoMapBuilder(self.files, self.config)

        ranked = builder.build(query="permission approval policy")

        self.assertEqual(ranked[0].path, "policy.py")

    def test_shared_view_cache_does_not_cross_workspace_indexes(self) -> None:
        other_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(other_temporary.cleanup)
        other_root = Path(other_temporary.name).resolve()
        other_files = self._files(other_root)
        cache = RepoMapViewCache()
        self.write("module.py", "def first_workspace():\n    pass\n")
        (other_root / "module.py").write_text(
            "def second_workspace():\n    pass\n",
            encoding="utf-8",
        )
        first = RepoMapBuilder(
            self.files,
            self.config,
            view_cache=cache,
        )
        second = RepoMapBuilder(
            other_files,
            ContextConfig(other_root, other_root, "System"),
            view_cache=cache,
        )

        first_rendered = first.render("workspace", (), 80)
        second_rendered = second.render("workspace", (), 80)

        self.assertIn("first_workspace", first_rendered)
        self.assertIn("second_workspace", second_rendered)
        self.assertIsNot(first.index.view_identity, second.index.view_identity)
        identities = tuple(key.index_identity for key in cache._entries)
        self.assertTrue(any(item is first.index.view_identity for item in identities))
        self.assertTrue(any(item is second.index.view_identity for item in identities))

    def test_view_cache_uses_raw_query_and_orderless_touched_set(self) -> None:
        self.write("a.py", "def alpha():\n    pass\n")
        self.write("b.py", "def beta():\n    pass\n")
        cache = RepoMapViewCache()
        builder = RepoMapBuilder(
            self.files,
            self.config,
            view_cache=cache,
        )

        first, first_hits, first_misses = builder.render_with_metrics(
            "Straße", ("a.py", "b.py"), 80
        )
        distinct, distinct_hits, distinct_misses = (
            builder.render_with_metrics(
                "STRASSE", ("a.py", "b.py"), 80
            )
        )
        reordered, reordered_hits, reordered_misses = (
            builder.render_with_metrics(
                "Straße", ("b.py", "a.py"), 80
            )
        )

        self.assertEqual((first_hits, first_misses), (0, 1))
        self.assertEqual((distinct_hits, distinct_misses), (0, 1))
        self.assertEqual((reordered_hits, reordered_misses), (1, 0))
        self.assertEqual(first, reordered)
        self.assertIsInstance(distinct, str)


if __name__ == "__main__":
    unittest.main()
