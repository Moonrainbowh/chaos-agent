from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from code_agent.interfaces.command_availability import available_services
from code_agent.interfaces.command_registry import REGISTRY
from code_agent.interfaces.tui_commands import TuiCommandKind, parse_tui_command
from code_agent.interfaces.tui_builtin_commands import handle_builtin_command
from code_agent.interfaces.tui_memory_commands import handle_memory_command
from code_agent.interfaces.windows_tui import WindowsTerminalApp
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.sessions.errors import SessionNotFound
from code_agent.sessions.models import MemoryLifecycle, MemoryRecord

from code_agent.interfaces.tests._support import FakeEngine


def record(revision: int = 1) -> MemoryRecord:
    return MemoryRecord("M1", revision, "project", "P1", "decision", "使用中文地质条件",
                        source_refs={"entry_origin": "user_explicit"},
                        conditions={"branch": "main"}, origin="user_explicit",
                        lifecycle=MemoryLifecycle.ACTIVE)


class MemoryParsingTests(TestCase):
    def test_service_is_required_and_does_not_expand_primary_commands(self):
        self.assertIsNotNone(parse_tui_command("/memory list", set()).error)
        self.assertIsNotNone(parse_tui_command("/memory list").error)
        self.assertNotIn("memory", [s.name for s in REGISTRY.primary({"project_memory"})])
        self.assertIn("project_memory", available_services(SimpleNamespace(project_memory=object())))

    def test_raw_body_preserves_quotes_spaces_and_json(self):
        text = 'save --conditions {"branch": "main"} -- 中文 "a"  b'
        outcome = parse_tui_command("/memory " + text, {"project_memory"})
        self.assertEqual(outcome.command.kind, TuiCommandKind.MEMORY)
        self.assertEqual(outcome.command.instruction, text)
        self.assertEqual(outcome.command.action, "save")
        self.assertEqual(parse_tui_command("/记忆", {"project_memory"}).command.action, "list")
        self.assertIsNotNone(parse_tui_command("/memory activate", {"project_memory"}).error)


class MemoryCommandTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.control = SimpleNamespace(**{
            name: AsyncMock(return_value=(record(),) if name in {"list", "search"} else record())
            for name in ("save", "list", "search", "show", "revise", "withdraw", "delete")})
        self.control.applicability = AsyncMock(return_value="applicable")
        self.engine = FakeEngine(())
        self.app = WindowsTerminalApp(AgentController(self.engine), ApprovalBroker(),
                                      project_memory=self.control, write=lambda _: None)
        self.app.current_thread_id = "T1"

    async def test_real_terminal_submit_routes_explicit_save_without_model(self):
        self.assertTrue(await self.app.submit('/memory save 中文 "a"  b'))
        self.control.save.assert_awaited_once_with('中文 "a"  b', conditions=None, thread_id="T1")
        self.assertEqual(self.engine.calls, [])
        self.assertIn("source_refs", self.app.state.entries[-1].text)
        self.assertIn('"applicability": "applicable"', self.app.state.entries[-1].text)

    async def test_conditions_and_revision_cas_preserve_source_default(self):
        outcome = parse_tui_command('/memory revise M1 --conditions {"branch":"main"} -- 新决策', {"project_memory"})
        self.assertTrue(await handle_builtin_command(self.app, outcome.command))
        self.control.show.assert_awaited_once_with("M1", thread_id="T1")
        self.control.revise.assert_awaited_once_with("M1", "新决策", expected_revision=1,
                                                    conditions={"branch": "main"}, thread_id="T1")
        self.assertTrue(await handle_memory_command(self.app, "withdraw M1"))
        self.control.withdraw.assert_awaited_once_with("M1", revision=1, thread_id="T1")

    async def test_list_search_show_delete_are_scoped(self):
        for command in ("list 20", "search 地质 条件", "show M1", "delete M1"):
            self.assertTrue(await handle_memory_command(self.app, command))
        self.control.list.assert_awaited_once_with(limit=20, offset=20, thread_id="T1")
        self.control.search.assert_awaited_once_with("地质 条件", limit=20, thread_id="T1")
        self.control.delete.assert_awaited_once_with("M1", thread_id="T1")

    async def test_invalid_conditions_and_arguments_do_not_write(self):
        for command in ("save", "save --conditions [] -- text", "save --conditions {} text",
                        "revise M1", "delete M1 M2", "list -1", "search"):
            self.assertFalse(await handle_memory_command(self.app, command))
        self.control.save.assert_not_awaited()
        self.control.revise.assert_not_awaited()
        self.control.delete.assert_not_awaited()

    async def test_foreign_scope_missing_id_and_cas_errors_are_safe(self):
        for error in (PermissionError("secret path"), SessionNotFound("secret SQL"),
                      ValueError("secret content"), RuntimeError("secret database")):
            self.control.show.side_effect = error
            self.assertFalse(await handle_memory_command(self.app, "revise M1 new"))
            self.assertNotIn("secret", self.app.state.entries[-1].text)
        self.control.revise.assert_not_awaited()

    async def test_memory_output_sanitizes_ansi_and_bounds_display(self):
        self.control.show.return_value = MemoryRecord("M2", 1, "project", "P1", "decision",
                                                       "\x1b[31m" + "中" * 20000)
        self.assertTrue(await handle_memory_command(self.app, "show M2"))
        output = self.app.state.entries[-1].text
        self.assertLess(len(output), 12100)
        self.assertNotIn("\x1b", output)

    async def test_applicability_is_host_fact_not_inferred_from_condition_json(self):
        for label in ("needs_check", "conflict", "withdrawn"):
            self.control.applicability.return_value = label
            self.assertTrue(await handle_memory_command(self.app, "show M1"))
            self.assertIn('"applicability": "' + label + '"', self.app.state.entries[-1].text)
        self.control.applicability.assert_awaited_with("M1", thread_id="T1")

    async def test_search_does_not_offer_unrelated_list_page(self):
        self.control.search.return_value = (record(),) * 20
        self.assertTrue(await handle_memory_command(self.app, "search 地质"))
        self.assertNotIn("下一页", self.app.state.entries[-1].text)
        self.control.list.return_value = (record(),) * 20
        self.assertTrue(await handle_memory_command(self.app, "list 20"))
        self.assertIn("/memory list 40", self.app.state.entries[-1].text)
