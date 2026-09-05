from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.agent_app_test_support import _configured_application


class SemanticApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_application_injects_shared_index_control_into_tui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            application, root, _ = _configured_application(Path(directory))
            try:
                (root / "sample.py").write_text("def sample_function():\n    return 1\n", encoding="utf-8")
                services = application.workspace_runtime.services_for_root(root)
                self.assertIs(services.repo_index, application.repo_index)
                application.tui._write = lambda _: None
                self.assertTrue(await application.tui.submit(":map overview sample.py"))
                rendered = application.tui.state.entries[-1].text
                self.assertIn("sample_function", rendered)
                self.assertIn("semantic generation 1", rendered)
                self.assertEqual(application.repo_index.snapshot().generation, 1)
                self.assertIsNone(application.tui.active_task_id)
            finally:
                await application.aclose()

    async def test_bound_task_uses_its_own_shared_index_not_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            application, root, _ = _configured_application(Path(directory))
            try:
                (root / "source_only.py").write_text("value = 1\n", encoding="utf-8")
                task_root = Path(directory) / "task-root"
                task_root.mkdir()
                (task_root / "task_only.py").write_text("def task_symbol():\n    return 2\n", encoding="utf-8")
                runtime = application.workspace_runtime
                runtime.bind_thread("mapped-thread", task_root)
                services = runtime.services_for_root(task_root)
                report = await application.tui.semantic_graph.analyze("overview", thread_id="mapped-thread")
                labels = {i.label for s in report.sections for i in s.items}
                self.assertIn("task_only.py", labels)
                self.assertNotIn("source_only.py", labels)
                self.assertIn(str(task_root.resolve()), report.warnings[0])
                self.assertIsNot(services.repo_index, application.repo_index)
                self.assertEqual(services.repo_index.snapshot().generation, report.generation)
            finally:
                await application.aclose()


if __name__ == "__main__":
    unittest.main()
