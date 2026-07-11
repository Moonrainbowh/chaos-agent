from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.models import ContextConfig  # noqa: E402
from code_agent.context.repo_map import RepoMapBuilder  # noqa: E402
from code_agent.context.tokens import estimate_tokens  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class RepoMapBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.guard = WorkspacePathGuard(self.root)
        self.files = WorkspaceFiles(
            self.guard, IgnoreRules.from_workspace(self.root)
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

    def builder(self, config: ContextConfig | None = None) -> RepoMapBuilder:
        return RepoMapBuilder(self.files, config or self.config)

    def entries_by_path(self) -> dict[str, object]:
        return {entry.path: entry for entry in self.builder().build()}

    def test_python_ast_extracts_nested_symbols_and_maps_local_imports(self) -> None:
        self.write("pkg/__init__.py", "")
        self.write("pkg/base.py", "class Base:\n    pass\n")
        self.write(
            "pkg/service.py",
            "import pkg.base\n"
            "from .base import Base\n\n"
            "class Service(Base):\n"
            "    async def run(self):\n"
            "        pass\n"
            "    def helper(self):\n"
            "        def inner():\n"
            "            return 1\n"
            "        return inner()\n",
        )

        entries = self.entries_by_path()
        service = entries["pkg/service.py"]

        self.assertEqual(
            [(item.name, item.kind, item.line) for item in service.symbols],
            [
                ("Service", "class", 4),
                ("Service.run", "async_function", 5),
                ("Service.helper", "function", 7),
                ("Service.helper.inner", "function", 8),
            ],
        )
        self.assertEqual(service.dependencies, ("pkg/base.py",))
        self.assertGreater(service.size_bytes, 0)

    def test_typescript_and_rust_use_conservative_line_declarations(self) -> None:
        self.write(
            "web/client.ts",
            "export interface Client {}\n"
            "export async function fetchData() {}\n"
            "class Worker {}\n",
        )
        self.write(
            "native/lib.rs",
            "pub struct Runner {}\n"
            "pub async fn execute() {}\n"
            "enum State { Ready }\n",
        )

        entries = self.entries_by_path()

        self.assertEqual(
            [(item.name, item.kind) for item in entries["web/client.ts"].symbols],
            [("Client", "interface"), ("fetchData", "function"), ("Worker", "class")],
        )
        self.assertEqual(
            [(item.name, item.kind) for item in entries["native/lib.rs"].symbols],
            [("Runner", "struct"), ("execute", "function"), ("State", "enum")],
        )

    def test_bad_source_files_degrade_to_path_entries(self) -> None:
        self.write("broken.py", "def incomplete(\n")
        (self.root / "invalid.ts").write_bytes(b"class Nope \xff")

        entries = self.entries_by_path()

        self.assertEqual(entries["broken.py"].symbols, ())
        self.assertEqual(entries["broken.py"].dependencies, ())
        self.assertEqual(entries["invalid.ts"].symbols, ())

    def test_ranking_uses_query_touched_files_and_dependency_indegree(self) -> None:
        self.write("a_importer.py", "import z_target\n")
        self.write("z_target.py", "def target():\n    pass\n")
        self.write("unrelated.py", "def other():\n    pass\n")

        by_query = self.builder().build(query="target")
        by_touch = self.builder().build(
            query="unrelated", touched_files=("a_importer.py",)
        )
        no_query = self.builder().build()

        self.assertEqual(by_query[0].path, "z_target.py")
        self.assertEqual(by_touch[0].path, "a_importer.py")
        self.assertLess(
            [entry.path for entry in no_query].index("z_target.py"),
            [entry.path for entry in no_query].index("a_importer.py"),
        )

    def test_render_is_deterministic_informative_and_strictly_budgeted(self) -> None:
        self.write("base.py", "class Base:\n    pass\n")
        self.write(
            "service_with_a_long_name.py",
            "from base import Base\n"
            "def exceptionally_long_service_function_name():\n    pass\n",
        )

        first = self.builder().render(
            query="service", touched_files=(), token_budget=32
        )
        second = self.builder().render(
            query="service", touched_files=(), token_budget=32
        )
        tiny = self.builder().render(
            query="service", touched_files=(), token_budget=3
        )

        self.assertEqual(first.encode("utf-8"), second.encode("utf-8"))
        self.assertIn("service_with_a_long_name.py", first)
        self.assertIn("exceptionally_long_service", first)
        self.assertLessEqual(estimate_tokens(first), 32)
        self.assertLessEqual(estimate_tokens(tiny), 3)
        self.assertEqual(self.builder().render("", (), 0), "")

    def test_repository_scan_never_returns_more_than_the_configured_limit(self) -> None:
        for index in range(8):
            self.write(f"file_{index}.py", "pass\n")
        limited = ContextConfig(
            self.root,
            self.root,
            "System",
            repo_scan=3,
            repo_map_tokens=50,
        )

        self.assertLessEqual(len(self.builder(limited).build()), 3)


if __name__ == "__main__":
    unittest.main()
