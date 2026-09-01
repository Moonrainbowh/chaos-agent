from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.context.repo_index import RepoIndexService
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard


class RepoSemanticGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.files = WorkspaceFiles(
            WorkspacePathGuard(self.root),
            IgnoreRules.from_workspace(self.root),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: str) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def test_resolves_src_relative_alias_reexport_call_and_inheritance(self) -> None:
        self.write("pyproject.toml", "[project]\nname='sample'\n")
        self.write("src/pkg/base.py", "class Base:\n    pass\n\ndef helper():\n    pass\n")
        self.write(
            "src/pkg/api.py",
            "from .base import Base as B, helper\n\n"
            "class Child(B):\n"
            "    def run(self):\n"
            "        return helper()\n",
        )
        self.write(
            "src/pkg/__init__.py",
            "from .api import Child\n__all__ = ['Child']\n",
        )
        self.write(
            "tests/test_api.py",
            "from pkg import Child\n\ndef test_child():\n    return Child()\n",
        )

        snapshot = RepoIndexService(self.files, max_files=100).snapshot_for_turn()
        by_path = {entry.path: entry for entry in snapshot.entries}
        api = by_path["src/pkg/api.py"]
        init = by_path["src/pkg/__init__.py"]
        test = by_path["tests/test_api.py"]

        api_edges = {
            (edge.kind, edge.target_path, edge.target_symbol, edge.resolution)
            for edge in api.relations
        }
        self.assertIn(("import", "src/pkg/base.py", "", "exact"), api_edges)
        self.assertIn(("inherits", "src/pkg/base.py", "Base", "exact"), api_edges)
        self.assertIn(("call", "src/pkg/base.py", "helper", "exact"), api_edges)
        self.assertIn(
            ("reference", "src/pkg/base.py", "helper", "exact"), api_edges
        )
        self.assertIn(
            ("import", "src/pkg/api.py", "Child", "exact"),
            {
                (edge.kind, edge.target_path, edge.target_symbol, edge.resolution)
                for edge in init.relations
            },
        )
        self.assertIn(
            ("call", "src/pkg/api.py", "Child", "exact"),
            {
                (edge.kind, edge.target_path, edge.target_symbol, edge.resolution)
                for edge in test.relations
            },
        )
        self.assertTrue(
            all(edge.kind != "test_impact" for entry in snapshot.entries for edge in entry.relations)
        )

    def test_star_import_never_creates_exact_symbol_relation(self) -> None:
        self.write("src/pkg/values.py", "def value():\n    pass\n")
        self.write("src/pkg/use.py", "from .values import *\n\ndef run():\n    return value()\n")

        snapshot = RepoIndexService(self.files, max_files=100).snapshot_for_turn()
        use = next(entry for entry in snapshot.entries if entry.path.endswith("use.py"))

        self.assertIn("src/pkg/values.py", use.dependencies)
        self.assertFalse(
            any(
                edge.target_symbol == "value" and edge.resolution == "exact"
                for edge in use.relations
            )
        )

    def test_same_config_key_in_distinct_namespaces_does_not_cross_link(self) -> None:
        self.write("a.py", "def load(config):\n    return config.get('timeout')\n")
        self.write("b.py", "def load(options):\n    return options.get('timeout')\n")

        snapshot = RepoIndexService(self.files, max_files=100).snapshot_for_turn()
        configs = [
            edge
            for entry in snapshot.entries
            for edge in entry.relations
            if edge.kind == "config"
        ]

        self.assertEqual(
            {(edge.config_namespace, edge.config_key) for edge in configs},
            {("mapping:config", "timeout"), ("mapping:options", "timeout")},
        )
        self.assertTrue(all(not edge.target_path for edge in configs))

    def test_matching_config_namespace_key_and_provenance_link_across_files(self) -> None:
        self.write("shared.py", "settings = {}\n")
        self.write("a.py", "from shared import settings\nvalue = settings.get('timeout')\n")
        self.write("b.py", "from shared import settings\nvalue = settings.get('timeout')\n")
        snapshot = RepoIndexService(self.files, max_files=100).snapshot_for_turn()
        a = next(item for item in snapshot.entries if item.path == "a.py")
        self.assertTrue(any(
            edge.kind == "config" and edge.target_path == "b.py"
            for edge in a.relations
        ))

    def test_same_bare_parameter_name_does_not_create_exact_config_peer(self) -> None:
        self.write("a.py", "def one(settings):\n    return settings.get('timeout')\n")
        self.write("b.py", "def two(settings):\n    return settings.get('timeout')\n")
        snapshot = RepoIndexService(self.files, max_files=100).snapshot_for_turn()
        self.assertFalse(any(
            edge.kind == "config" and edge.target_path
            for entry in snapshot.entries
            for edge in entry.relations
        ))

    def test_relative_config_imports_from_different_packages_do_not_cross_link(self) -> None:
        for package in ("p1", "p2"):
            self.write(f"{package}/__init__.py", "")
            self.write(f"{package}/shared.py", "settings = {}\n")
            self.write(
                f"{package}/use.py",
                "from .shared import settings\nvalue = settings.get('timeout')\n",
            )
        snapshot = RepoIndexService(self.files, max_files=100).snapshot_for_turn()
        p1 = next(item for item in snapshot.entries if item.path == "p1/use.py")
        self.assertFalse(any(
            edge.kind == "config" and edge.target_path == "p2/use.py"
            for edge in p1.relations
        ))

    def test_function_imports_and_nested_definitions_resolve_by_lexical_scope(self) -> None:
        self.write("a.py", "class X: pass\n")
        self.write("b.py", "class X: pass\n")
        self.write(
            "use.py",
            "def one():\n"
            "    from a import X\n"
            "    def helper():\n"
            "        return X()\n"
            "    return helper()\n\n"
            "def two():\n"
            "    from b import X\n"
            "    return X()\n\n"
            "def three():\n"
            "    return X()\n",
        )
        snapshot = RepoIndexService(self.files, max_files=100).snapshot_for_turn()
        use = next(item for item in snapshot.entries if item.path == "use.py")
        calls = {
            (edge.source_symbol, edge.target_path, edge.target_symbol)
            for edge in use.relations if edge.kind == "call"
        }
        self.assertIn(("one.helper", "a.py", "X"), calls)
        self.assertIn(("one", "use.py", "one.helper"), calls)
        self.assertIn(("two", "b.py", "X"), calls)
        self.assertFalse(any(source == "three" for source, _, _ in calls))

    def test_ambiguous_module_roots_and_dynamic_all_are_not_exact(self) -> None:
        self.write("pkg/mod.py", "class X: pass\n")
        self.write("src/pkg/mod.py", "class X: pass\n")
        self.write("use.py", "from pkg.mod import X\nX()\n")
        self.write("api.py", "class Hidden: pass\n")
        self.write(
            "pkg/__init__.py",
            "from api import Hidden\n__all__ = build_exports()\n",
        )
        self.write("consumer.py", "from pkg import Hidden\nHidden()\n")
        snapshot = RepoIndexService(self.files, max_files=100).snapshot_for_turn()
        for path in ("use.py", "consumer.py"):
            entry = next(item for item in snapshot.entries if item.path == path)
            self.assertFalse(any(
                edge.resolution == "exact" and edge.target_symbol in {"X", "Hidden"}
                for edge in entry.relations
            ))


if __name__ == "__main__":
    unittest.main()
