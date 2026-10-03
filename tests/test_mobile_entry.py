from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from chaos_agent.mobile_catalog import MobileCatalog, ProjectSessionView
from chaos_agent.mobile_cli import run_mobile
from code_agent.project_launcher.store import ProjectStore
from code_agent.sessions.repository import SQLiteSessionRepository


class MobileEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.first, self.second = self.base / "one", self.base / "two"
        self.first.mkdir(); self.second.mkdir()
        self.store = ProjectStore(self.base / "projects.json")
        self.store.add(self.first); self.store.add(self.second)
        self.catalog = SimpleNamespace(threads=AsyncMock(return_value=()))

    def application(self):
        return SimpleNamespace(tui=SimpleNamespace(run=AsyncMock()), startup=AsyncMock(), aclose=AsyncMock())

    async def test_project_change_closes_before_constructing_new_runtime(self):
        applications, sequence = [], []
        def factory(root):
            if applications:
                self.assertEqual(applications[-1].aclose.await_count, 1)
            app = self.application()
            sequence.append(root)
            async def run(**kwargs):
                if root == self.first:
                    app.tui.requested_project = self.second
            app.tui.run.side_effect = run
            applications.append(app)
            return app
        chooser = AsyncMock(return_value=self.first)
        self.assertEqual(await run_mobile(self.store, self.catalog, factory=factory, chooser=chooser), 0)
        self.assertEqual(sequence, [self.first, self.second])
        self.assertEqual(chooser.await_count, 1)
        for app in applications:
            app.aclose.assert_awaited_once()
            self.assertIs(app.tui.project_store, self.store)
        self.assertEqual(self.store.last_root(), self.second)

    async def test_factory_failure_returns_to_menu_without_old_cwd_fallback(self):
        roots = []
        def factory(root):
            roots.append(root)
            raise RuntimeError("unavailable")
        chooser = AsyncMock(side_effect=[self.second, None])
        self.assertEqual(await run_mobile(self.store, self.catalog, factory=factory, chooser=chooser), 0)
        self.assertEqual(roots, [self.second])
        self.assertEqual(chooser.await_count, 2)

    async def test_close_failure_prevents_constructing_the_next_project(self):
        app = self.application()
        app.aclose.side_effect = RuntimeError("close failed")
        async def run():
            app.tui.requested_project = self.second
        app.tui.run.side_effect = run
        roots = []
        def factory(root):
            roots.append(root)
            return app
        result = await run_mobile(self.store, self.catalog, factory=factory,
                                  chooser=AsyncMock(return_value=self.first))
        self.assertEqual(result, 2)
        self.assertEqual(roots, [self.first])
        self.assertEqual(self.store.last_root(), self.first)

    async def test_startup_failure_closes_partial_application_and_returns_to_menu(self):
        app = self.application()
        app.startup.side_effect = RuntimeError("unavailable")
        chooser = AsyncMock(side_effect=[self.first, None])
        await run_mobile(self.store, self.catalog, factory=lambda root: app, chooser=chooser)
        app.aclose.assert_awaited_once()
        app.tui.run.assert_not_awaited()
        self.assertEqual(chooser.await_count, 2)

    async def test_project_opens_without_implicitly_resuming_an_old_task(self):
        app = self.application()
        self.catalog.threads.return_value = (SimpleNamespace(id="existing-thread"),)
        await run_mobile(self.store, self.catalog, factory=lambda root: app, chooser=AsyncMock(return_value=self.first))
        app.tui.run.assert_awaited_once_with()
        self.catalog.threads.assert_not_awaited()
        self.assertFalse(app.tui.composer_expanded)

    async def test_project_history_is_filtered_and_cross_project_read_rejected(self):
        repo = SQLiteSessionRepository(self.base / "sessions.sqlite3")
        self.addCleanup(repo.close)
        ids = []
        for root in (self.first, self.second):
            thread = await repo.create_thread()
            await repo.create_checkpoint(thread, "remote-session", {"source_root": str(root)})
            ids.append(thread)
        view = ProjectSessionView(MobileCatalog(repo), self.first)
        rows = await view.list_threads()
        self.assertEqual([row.id for row in rows], [ids[0]])
        self.assertEqual(await view.load_messages(ids[0]), ())
        with self.assertRaises(ValueError):
            await view.load_messages(ids[1])

    async def test_empty_launcher_cancel_does_not_construct_an_application(self):
        calls = []
        self.assertEqual(await run_mobile(self.store, self.catalog, factory=lambda root: calls.append(root), chooser=AsyncMock(return_value=None)), 0)
        self.assertEqual(calls, [])
