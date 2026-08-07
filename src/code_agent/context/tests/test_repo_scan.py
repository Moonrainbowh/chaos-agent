from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.repo_scan import RepoFileScanner  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class RepoFileScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        guard = WorkspacePathGuard(self.root)
        self.files = WorkspaceFiles(
            guard, IgnoreRules.from_workspace(self.root)
        )
        self.scanner = RepoFileScanner(self.files)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: str) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def test_scans_one_python_file_into_signature_symbols_and_raw_imports(self) -> None:
        self.write(
            "pkg/service.py",
            "import pkg.base\n"
            "from .helpers import run\n\n"
            "class Service:\n"
            "    async def execute(self):\n"
            "        return run()\n",
        )

        facts = self.scanner.scan("pkg/service.py")

        self.assertEqual(facts.path, "pkg/service.py")
        self.assertGreater(facts.signature.size_bytes, 0)
        self.assertGreater(facts.signature.modified_ns, 0)
        self.assertEqual(
            [(item.name, item.kind, item.line) for item in facts.symbols],
            [
                ("Service", "class", 4),
                ("Service.execute", "async_function", 5),
            ],
        )
        self.assertEqual(
            [
                (item.module, item.level, item.names)
                for item in facts.imports
            ],
            [
                ("pkg.base", 0, ()),
                ("helpers", 1, ("run",)),
            ],
        )

    def test_syntax_and_binary_failures_publish_path_only_facts(self) -> None:
        self.write("broken.py", "def incomplete(\n")
        (self.root / "binary.ts").write_bytes(b"class Nope \xff")

        broken = self.scanner.scan("broken.py")
        binary = self.scanner.scan("binary.ts")

        self.assertEqual(broken.symbols, ())
        self.assertEqual(broken.imports, ())
        self.assertEqual(binary.symbols, ())
        self.assertEqual(binary.imports, ())
        self.assertGreater(broken.signature.size_bytes, 0)
        self.assertGreater(binary.signature.size_bytes, 0)

    def test_signature_changes_when_the_file_changes(self) -> None:
        self.write("module.py", "def first():\n    pass\n")
        first = self.scanner.scan("module.py")
        self.write(
            "module.py",
            "def second_with_a_longer_name():\n    pass\n",
        )

        second = self.scanner.scan("module.py")

        self.assertNotEqual(first.signature, second.signature)
        self.assertEqual(second.symbols[0].name, "second_with_a_longer_name")

    def test_text_body_is_available_for_bounded_lexical_indexing(self) -> None:
        self.write("AGENTS.md", "危险命令执行前进行权限校验。\n")
        (self.root / "binary.bin").write_bytes(b"\0not text")

        text = self.scanner.scan("AGENTS.md")
        binary = self.scanner.scan("binary.bin")

        self.assertIn("权限校验", text.search_text)
        self.assertEqual(binary.search_text, "")

    def test_long_search_body_keeps_the_real_file_tail(self) -> None:
        self.write(
            "large.txt",
            "HEAD_MARKER\n" + ("middle\n" * 4_000) + "TAIL_MARKER\n",
        )

        facts = self.scanner.scan("large.txt")

        self.assertLessEqual(len(facts.search_text), 16_000)
        self.assertIn("HEAD_MARKER", facts.search_text)
        self.assertIn("TAIL_MARKER", facts.search_text)


if __name__ == "__main__":
    unittest.main()
