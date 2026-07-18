from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.models import ContextConfig  # noqa: E402
from code_agent.context.repo_index import RepoIndexService  # noqa: E402
from code_agent.context.repo_map import (  # noqa: E402
    RepoMapBuilder,
    RepoMapViewCache,
)
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class RepoMapTurnViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        guard = WorkspacePathGuard(self.root)
        self.files = WorkspaceFiles(
            guard, IgnoreRules.from_workspace(self.root)
        )
        self.config = ContextConfig(
            self.root,
            self.root,
            "System",
            repo_scan=100,
            repo_map_tokens=200,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: str) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def builder(
        self, view_cache: RepoMapViewCache | None = None
    ) -> RepoMapBuilder:
        return RepoMapBuilder(
            self.files,
            self.config,
            view_cache=view_cache,
        )

    def test_turn_query_does_not_touch_filesystem_after_index_initialization(self) -> None:
        self.write("alpha.py", "def alpha():\n    pass\n")
        self.write("target.py", "def target():\n    pass\n")
        builder = self.builder()
        builder.build(query="alpha")

        with patch.object(
            self.files,
            "list_files",
            side_effect=AssertionError("turn view must not list files"),
        ), patch.object(
            self.files,
            "read_text",
            side_effect=AssertionError("turn view must not read files"),
        ):
            ranked = builder.build(query="target")

        self.assertEqual(ranked[0].path, "target.py")

    def test_render_cache_uses_generation_query_touches_and_budget(self) -> None:
        self.write("module.py", "def first():\n    pass\n")
        builder = self.builder()

        first, first_hits, first_misses = builder.render_with_metrics(
            "first", ("module.py",), 40
        )
        second, second_hits, second_misses = builder.render_with_metrics(
            "first", ("module.py",), 40
        )

        self.assertEqual(first, second)
        self.assertEqual((first_hits, first_misses), (0, 1))
        self.assertEqual((second_hits, second_misses), (1, 0))
        self.write(
            "module.py",
            "def second_with_a_longer_name():\n    pass\n",
        )
        builder.invalidate(("module.py",))

        updated, updated_hits, updated_misses = builder.render_with_metrics(
            "second_with_a_longer_name", ("module.py",), 40
        )

        self.assertIn("second_with_a_longer_name", updated)
        self.assertEqual((updated_hits, updated_misses), (0, 1))

    def test_render_cache_is_bounded_lru(self) -> None:
        self.write("module.py", "def module():\n    pass\n")
        cache = RepoMapViewCache(max_entries=2)
        builder = self.builder(cache)

        builder.render_with_metrics("first", (), 20)
        builder.render_with_metrics("second", (), 20)
        builder.render_with_metrics("third", (), 20)
        _, hits, misses = builder.render_with_metrics("first", (), 20)

        self.assertEqual(len(cache), 2)
        self.assertEqual((hits, misses), (0, 1))

    def test_shared_index_does_not_construct_an_unused_facade_scanner(self) -> None:
        index = RepoIndexService(self.files)

        with patch(
            "code_agent.context.repo_map.RepoFileScanner",
            side_effect=AssertionError("shared index already owns its scanner"),
        ):
            builder = RepoMapBuilder(
                self.files,
                self.config,
                index=index,
            )

        self.assertIs(builder.index, index)


if __name__ == "__main__":
    unittest.main()
