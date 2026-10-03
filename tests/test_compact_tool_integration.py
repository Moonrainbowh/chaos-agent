import tempfile
import unittest
from pathlib import Path
import httpx
from code_agent.core.engine import AgentEngine
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest, ModelEvent, ModelEventKind, ToolCall
from code_agent.core.tests._engine_support import FakeContextBuilder, FakeModelClient
from code_agent.capabilities.catalog import progressive_tools
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.policy.models import ApprovalMode
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.web_access import WebAccessService
from chaos_agent.tools import tool_definitions
from chaos_agent.action_dispatcher import RootActionDispatcher
from chaos_agent.restricted_dispatcher import RestrictedDispatcher


def stream(*calls):
    return [*(ModelEvent(ModelEventKind.TOOL_CALL,tool_call=call) for call in calls),ModelEvent(ModelEventKind.COMPLETED)]


class CompactIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_application_exposes_compact_tools_and_web(self):
        from tests.test_tui_repair_integration import application_fixture
        with application_fixture() as app:
            try:
                actions = app.controller._engine._actions
                self.assertEqual({t.name for t in progressive_tools(actions.tools(), {})},
                    {"read", "search", "write", "edit", "execute", "load_tool_contract"})
                self.assertIn("web", {t.name for t in actions.tools()})
                request = actions.resolve_action(ActionRequest("id", "read", {"operation":"file", "path":"README.md"}))
                self.assertEqual(request.name, "read_file")
                with self.assertRaisesRegex(ValueError, "outside"):
                    actions.resolve_action(ActionRequest("git", "execute", {"operation":"status"}))
                self.assertIsInstance(app.dispatcher.web_access, WebAccessService)
            finally:
                await app.aclose()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        guard = WorkspacePathGuard(self.root)
        self.seen = []
        def response(request):
            self.seen.append(str(request.url))
            return httpx.Response(200,text="<title>Page</title>verified-body")
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(response))
        self.addAsyncCleanup(self.http.aclose)
        self.dispatcher = RootActionDispatcher(WorkspaceFiles(guard,IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard),ActionPolicy(PolicyConfig(ApprovalMode.UNRESTRICTED,workspace_root=self.root)),
            ApprovalBroker(),web_access=WebAccessService(self.http))
        self.compact = RestrictedDispatcher(self.dispatcher,[t.name for t in tool_definitions(include_web=True)],compact_tools=True)
        self.sessions = SQLiteSessionRepository(self.root / "sessions.sqlite3")
        self.addCleanup(self.sessions.close)

    async def test_default_five_then_web_loaded_and_real_request_pairing(self):
        first = progressive_tools(self.compact.tools(),{})
        self.assertEqual({t.name for t in first},{"read","search","write","edit","execute","load_tool_contract"})
        model = FakeModelClient([stream(ToolCall("load","load_tool_contract",{"name":"web"})),
            stream(ToolCall("fetch","web",{"operation":"fetch","url":"https://example.test/page"})),
            [ModelEvent(ModelEventKind.TEXT_DELTA,text="done"),ModelEvent(ModelEventKind.COMPLETED)]])
        engine = AgentEngine(model,FakeContextBuilder(),self.compact,self.sessions)
        events = [event async for event in engine.run("Read the webpage")]
        self.assertNotIn("web",{t.name for t in model.calls[0][2]})
        self.assertIn("web",{t.name for t in model.calls[1][2]})
        self.assertEqual(self.seen,["https://example.test/page"])
        messages = await self.sessions.load_messages(events[0].payload["thread_id"])
        result = next(m for m in messages if m.tool_call_id == "fetch")
        self.assertEqual(result.name,"web")
        self.assertIn("verified-body",result.content)

    async def test_compact_write_and_replace_keep_actual_file_and_task_state(self):
        model = FakeModelClient([stream(ToolCall("write","write",{"operation":"file","path":"a.txt","content":"old"})),
            stream(ToolCall("replace","edit",{"operation":"replace","path":"a.txt","old_text":"old","new_text":"new"})),
            [ModelEvent(ModelEventKind.TEXT_DELTA,text="done"),ModelEvent(ModelEventKind.COMPLETED)]])
        events = [event async for event in AgentEngine(model,FakeContextBuilder(),self.compact,self.sessions).run("Edit file")]
        self.assertEqual((self.root / "a.txt").read_text(),"new")
        state = await self.sessions.load_task_state(events[0].payload["thread_id"])
        self.assertIn("a.txt",state.files_changed)

    async def test_read_only_compaction_does_not_add_apply_or_write(self):
        restricted = RestrictedDispatcher(self.dispatcher,["read_file","plan_workspace_edits_v1"],compact_tools=True)
        self.assertEqual({t.name for t in restricted.tools()},{"read","edit","load_tool_contract"})
        result = await restricted.dispatch(ActionRequest("id","edit",{"operation":"apply","plan_id":"x"}),CancellationToken())
        self.assertTrue(result.is_error)
        self.assertEqual(result.name,"edit")
        result = await restricted.dispatch(ActionRequest("old","write_file",{"path":"a","content":"x"}),CancellationToken())
        self.assertTrue(result.is_error)

    async def test_network_policy_is_still_enforced(self):
        self.dispatcher.policy = ActionPolicy(PolicyConfig(ApprovalMode.AUTO,workspace_root=self.root))
        result = await self.compact.dispatch(ActionRequest("id","web",{"operation":"fetch","url":"https://example.test"}),CancellationToken())
        self.assertTrue(result.is_error)
        self.assertEqual(self.seen,[])

    async def test_wrong_operation_fields_are_rejected_before_io(self):
        result = await self.compact.dispatch(ActionRequest("id","read",{"operation":"file","path":"a","content":"x"}),CancellationToken())
        self.assertTrue(result.is_error)
        self.assertFalse((self.root / "a").exists())
