from __future__ import annotations

import unittest

from code_agent.context.models import RepoEntry
from code_agent.context.errors import RepoMapError
from code_agent.context.repo_index import RepoIndexSnapshot
from code_agent.semantic_insights import SemanticInsightService
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.semantic_insight_view import format_semantic_insight
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp
from code_agent.workspace.errors import WorkspaceError


class _Control:
    async def analyze(self, kind, arguments=(), *, thread_id=None, **options):
        self.called = kind, arguments, thread_id, options
        return SemanticInsightService().analyze(
            RepoIndexSnapshot(3, (RepoEntry("src/a.py"),)), "overview"
        )


class SemanticInsightCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None
        )
        self.control = _Control()
        self.app.semantic_graph = self.control
        self.app.current_thread_id = "active"

    async def test_all_action_aliases_strip_the_original_token(self) -> None:
        cases = (("/map bug crash", "locate", ("crash",)),
                 (":图谱 tree src", "overview", ("src",)),
                 ("/repo-map dead src", "dead-code", ("src",)))
        for text, kind, arguments in cases:
            with self.subTest(text=text):
                self.assertTrue(await self.app.submit(text))
                self.assertEqual(self.control.called, (kind, arguments, "active", {}))

    async def test_quotes_paging_and_literal_flags_reach_control(self) -> None:
        self.assertTrue(await self.app.submit(':map impact "src/my file.py" --limit=50 --offset=12'))
        self.assertEqual(self.control.called, (
            "impact", ("src/my file.py",), "active", {"limit": 50, "offset": 12},
        ))
        self.assertTrue(await self.app.submit(":map locate -- --retry-failed"))
        self.assertEqual(self.control.called[1], ("--retry-failed",))

    async def test_bad_quotes_and_options_fail_without_controller_call(self) -> None:
        for text in (':map impact "unfinished', ':map overview --limit=x',
                     ':map overview --unknown=2', ':map overview --limit=2 --limit=3'):
            with self.subTest(text=text):
                self.assertFalse(await self.app.submit(text))
                self.assertFalse(hasattr(self.control, "called"))

    async def test_control_error_does_not_start_a_model_turn(self) -> None:
        async def fail(*args, **kwargs):
            raise RuntimeError("index unavailable")
        self.control.analyze = fail
        self.assertFalse(await self.app.submit(":map overview"))
        self.assertIn("index unavailable", self.app.state.entries[-1].text)
        self.assertIsNone(self.app.active_task_id)

    async def test_index_and_workspace_failures_are_recoverable(self) -> None:
        for error_type in (RepoMapError, WorkspaceError):
            async def fail(*args, **kwargs):
                raise error_type("bounded scan failed")
            self.control.analyze = fail
            self.assertFalse(await self.app.submit(":map overview"))
            self.assertIn("bounded scan failed", self.app.state.entries[-1].text)

    def test_view_discloses_page_count_and_preserves_global_numbering(self) -> None:
        snapshot = RepoIndexSnapshot(1, tuple(RepoEntry(f"src/{n}.py") for n in range(5)))
        report = SemanticInsightService().analyze(snapshot, "overview", limit=2, offset=2)
        rendered = format_semantic_insight(report)
        self.assertIn("Showing 3-4 of 5", rendered)
        self.assertIn("3. src/2.py", rendered)
        self.assertIn("semantic generation 1", rendered)


if __name__ == "__main__":
    unittest.main()
