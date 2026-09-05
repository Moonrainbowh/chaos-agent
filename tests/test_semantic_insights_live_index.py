from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from code_agent.context.repo_index import RepoIndexService
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent_win.semantic_insights import SemanticGraphControl


class LiveSemanticIndexTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "tests").mkdir()
        (self.root / "core.py").write_text("def compute():\n    return 'opaque failure marker'\n", encoding="utf-8")
        (self.root / "consumer.py").write_text("from core import compute\ndef run():\n    return compute()\n", encoding="utf-8")
        (self.root / "tests/test_core.py").write_text("from core import compute\ndef test_core():\n    assert compute()\n", encoding="utf-8")
        self.index = RepoIndexService(WorkspaceFiles(
            WorkspacePathGuard(self.root), IgnoreRules.from_workspace(self.root)
        ))
        self.addCleanup(self.index.close)
        runtime = SimpleNamespace(services_for_root=lambda root: SimpleNamespace(repo_index=self.index))
        self.control = SemanticGraphControl(self.root, runtime)

    async def test_real_parser_graph_fts_and_all_nine_user_commands(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        app.semantic_graph = self.control
        commands = (":map overview", ":map context compute", ":map impact core.py",
                    ":map tests core.py", ":map risk core.py", ":map review core.py",
                    ":map refactor core.py", ':map locate "opaque failure marker"',
                    ":map dead-code", ":map tree core.py", ":map bug compute")
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(await app.submit(command))
                self.assertIn("semantic generation 1", app.state.entries[-1].text)
        self.assertIsNone(app.active_task_id)
        self.assertEqual(self.index.snapshot().generation, 1)
        report = await self.control.analyze("tests", ("core.py",))
        self.assertEqual(report.sections[0].items[0].label, "tests/test_core.py")
        self.assertIn("distance 1", report.sections[0].items[0].detail)
        report = await self.control.analyze("locate", ("opaque failure marker",))
        self.assertEqual(report.sections[0].items[0].label, "core.py")

    async def test_external_edit_add_delete_refreshes_shared_generation(self) -> None:
        first = await self.control.analyze("overview")
        (self.root / "core.py").write_text("def renamed_operation():\n    return 'new marker'\n", encoding="utf-8")
        second = await self.control.analyze("context", ("renamed_operation",))
        self.assertGreater(second.generation, first.generation)
        self.assertEqual(second.sections[0].items[0].label, "core.py")
        (self.root / "extra.py").write_text("from core import renamed_operation\n", encoding="utf-8")
        third = await self.control.analyze("impact", ("core.py",))
        self.assertGreater(third.generation, second.generation)
        self.assertIn("extra.py", [i.label for i in third.sections[1].items])
        (self.root / "extra.py").unlink()
        fourth = await self.control.analyze("overview")
        self.assertGreater(fourth.generation, third.generation)
        self.assertNotIn("extra.py", self.index.snapshot().semantic_graph.nodes)
        with self.assertRaisesRegex(ValueError, "not present"):
            await self.control.analyze("risk", ("extra.py",))

    async def test_paging_and_unchanged_refresh_reuse_generation(self) -> None:
        first = await self.control.analyze("overview", limit=1)
        second = await self.control.analyze("overview", limit=1, offset=1)
        self.assertEqual(first.generation, second.generation)
        files = next(s for s in second.sections if s.title == "Files")
        self.assertEqual((len(files.items), files.total, files.offset), (1, 3, 1))

    async def test_missing_index_is_reported_without_fallback_scanning(self) -> None:
        control = SemanticGraphControl(self.root, SimpleNamespace(
            services_for_root=lambda root: SimpleNamespace(repo_index=None)
        ))
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            await control.analyze("overview")


if __name__ == "__main__":
    unittest.main()
