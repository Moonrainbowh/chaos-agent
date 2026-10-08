"""Exercise real command execution and model-visible branch history together."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from code_agent.core.cancellation import CancellationToken
from code_agent.core.engine import AgentEngine
from code_agent.core.models import Message, ModelEvent, ModelEventKind, ToolCall
from code_agent.core.task import TaskAuthorization, TaskContract
from code_agent.core.tests._engine_support import FakeContextBuilder, FakeModelClient
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.interfaces.windows_tui import WindowsTerminalApp
from code_agent.interfaces.tui_lifecycle import close_tasks
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.runtime.posix import PosixLocalRuntime
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.ignore import IgnoreRules
from chaos_agent.action_dispatcher import RootActionDispatcher
from chaos_agent.user_command_control import UserCommandControl
from chaos_agent.conversation_tree_control import ConversationTreeControl
from chaos_agent.conversation_controls import tool_catalog


class ConversationControlsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.sessions = SQLiteSessionRepository(self.root / "sessions.sqlite3")
        self.addCleanup(self.sessions.close)
        guard = WorkspacePathGuard(self.root)
        self.capture = SimpleNamespace(record_gap=AsyncMock())
        runtime_type = WindowsLocalRuntime if os.name == "nt" else PosixLocalRuntime
        self.dispatcher = RootActionDispatcher(
            WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root)),
            WorkspaceEditor(guard), ActionPolicy(PolicyConfig(workspace_root=self.root)),
            ApprovalBroker(), runtime=runtime_type(self.root), capture=self.capture,
        )
        self.commands = UserCommandControl(self.sessions,self.dispatcher)

    async def test_command_runs_without_model_and_next_request_receives_output(self):
        model = FakeModelClient([[ModelEvent(ModelEventKind.TEXT_DELTA,text="received"),
                                  ModelEvent(ModelEventKind.COMPLETED)]])
        engine = AgentEngine(model,FakeContextBuilder(),self.dispatcher,self.sessions)
        app = WindowsTerminalApp(AgentController(engine),ApprovalBroker(),
                                 sessions=self.sessions,history=self.sessions,write=lambda _:None)
        self.addAsyncCleanup(close_tasks,app)
        app.user_commands = self.commands
        command = "Write-Output 'shell-context-marker'" if os.name == "nt" else "printf shell-context-marker"
        self.assertTrue(await app.submit("!"+command))
        await app._run_task
        self.assertEqual(model.calls,[])
        self.capture.record_gap.assert_awaited_once()
        context = self.capture.record_gap.await_args.args[0]
        self.assertEqual(context.owner_thread_id,app.current_thread_id)
        self.assertIn("shell-context-marker",(await self.sessions.load_messages(app.current_thread_id))[-1].content)
        self.assertTrue(await app.submit("Explain the result"))
        await app._run_task
        self.assertEqual(len(model.calls),1)
        catalog = tool_catalog(app.controller)
        self.assertIn("最近请求实际可见",catalog)
        self.assertEqual(engine.last_advertised_tool_names,frozenset(t.name for t in model.calls[0][2]))
        self.assertTrue(any(m.name == "user_shell_result" and "shell-context-marker" in m.content
                            for m in model.calls[0][1]))

    async def test_double_bang_excludes_output_and_cancelled_command_is_durable(self):
        thread = await self.sessions.create_thread()
        command = "Write-Output 'private-marker'" if os.name == "nt" else "printf private-marker"
        _,result = await self.commands.run(command,thread,CancellationToken(),include=False)
        self.assertFalse(result.is_error)
        self.assertIn("private-marker",self.commands.display(result))
        self.assertEqual(await self.sessions.load_messages(thread),())
        self.assertEqual(await self.sessions.load_events(thread),())
        token = CancellationToken()
        token.cancel("cancel before launch")
        _,result = await self.commands.run(command,thread,token)
        self.assertEqual(result.output["reason"],"cancelled")
        self.assertEqual(len(await self.sessions.load_messages(thread)),2)

    async def test_tree_selected_path_is_the_only_history_sent_to_model(self):
        source = await self.sessions.create_thread()
        for role,text in (("user","root"),("assistant","shared"),("user","old-question"),("assistant","discarded-answer")):
            await self.sessions.append_message(source,Message(role,text))
        tree = ConversationTreeControl(self.sessions,None)
        node = (await tree.load(source)).nodes[2]
        branch,text = await tree.select(source,node)
        self.assertEqual(text,"old-question")
        await tree.label(branch,node.id,"saved")
        model = FakeModelClient([[ModelEvent(ModelEventKind.TEXT_DELTA,text="new-answer"),
                                  ModelEvent(ModelEventKind.COMPLETED)]])
        engine = AgentEngine(model,FakeContextBuilder(),self.dispatcher,self.sessions)
        async for _ in engine.run("new-question",thread_id=branch):
            pass
        self.assertEqual([m.content for m in model.calls[0][1]],["root","shared","new-question"])
        self.assertEqual((await self.sessions.load_messages(source))[-1].content,"discarded-answer")
        self.assertEqual(len((await tree.load(branch)).nodes),6)

    async def test_running_command_can_be_cancelled_with_result_preserved(self):
        token = CancellationToken()
        command = "Start-Sleep -Seconds 30" if os.name == "nt" else "sleep 30"
        execution = asyncio.create_task(self.commands.run(command,None,token))
        try:
            await asyncio.sleep(1)
            self.assertFalse(execution.done())
            token.cancel("test interrupt")
            thread,result = await asyncio.wait_for(execution,10)
        finally:
            token.cancel("test cleanup")
            await asyncio.gather(execution,return_exceptions=True)
        self.assertEqual(result.output["reason"],"cancelled")
        messages = await self.sessions.load_messages(thread)
        self.assertEqual(messages[-1].name,"user_shell_result")
        self.assertIn("cancelled",messages[-1].content)

    async def test_invalid_tree_position_does_not_change_runtime_settings(self):
        source = await self.sessions.create_thread()
        task = await self.sessions.create_task(source,TaskContract("old",TaskAuthorization.local_workspace(str(self.root))))
        await self.sessions.append_message(source,Message("assistant","",tool_calls=(ToolCall("call","read_file",{"path":"a"}),)))
        tasks = SimpleNamespace(_root=self.root,_task_source_root=AsyncMock(return_value=self.root),restore_runtime_settings=AsyncMock())
        tree = ConversationTreeControl(self.sessions,tasks)
        node = (await tree.load(source)).nodes[0]
        with self.assertRaises(ValueError):
            await tree.select(source,node)
        tasks.restore_runtime_settings.assert_not_awaited()
        await self.sessions.append_message(source,Message("tool","done",tool_call_id="call"))
        branch,_ = await tree.select(source,(await tree.load(source)).nodes[-1])
        tasks.restore_runtime_settings.assert_awaited_once_with(task.id)
        self.assertIsNone(await self.sessions.load_task_for_thread(branch))


if __name__ == "__main__":
    unittest.main()
