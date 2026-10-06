"""ACP prompts use the actual Application/Core and durable SQLite task facts."""
import asyncio
import tempfile
import unittest
from pathlib import Path

from acp import text_block

from chaos_agent.acp_task_service import AcpTaskService
from code_agent.acp import ChaosAcpAgent
from code_agent.core.events import EventKind
from code_agent.core.models import Message, ModelEvent, ModelEventKind, ToolCall, Usage
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from tests.agent_app_test_support import _isolated_application
from unittest.mock import AsyncMock, patch
from chaos_agent.acp_adapter import serve_acp
from code_agent.sessions.repository import SQLiteSessionRepository


class Reply:
    def __init__(self, *, usage=True, held=False):
        self.calls = 0
        self.usage, self.held = usage, held
        self.started = asyncio.Event()

    async def stream(self, *args):
        self.calls += 1
        self.started.set()
        if self.held:
            await asyncio.Event().wait()
        yield ModelEvent(ModelEventKind.TEXT_DELTA, text="Answer")
        if self.usage:
            yield ModelEvent(ModelEventKind.USAGE, usage=Usage(50, 2))
        yield ModelEvent(ModelEventKind.COMPLETED)


class Client:
    def __init__(self):
        self.updates = []

    async def session_update(self, session_id, update):
        self.updates.append((session_id, update))


class AcpTaskServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app, self.root, self.product = _isolated_application(Path(self.temp.name))
        self.reply = Reply()
        self.app.controller._engine._model.model = self.reply
        self.bridge = AcpTaskService(self.app.tasks, self.app.sessions, self.root)
        self.client = Client()
        self.agent = self.new_agent()
        self.session = (await self.agent.new_session(str(self.root))).session_id

    def new_agent(self):
        bridge = AcpTaskService(self.app.tasks, self.app.sessions, self.root)
        agent = ChaosAcpAgent(bridge, bridge, self.root)
        agent.on_connect(self.client)
        return agent

    async def asyncTearDown(self):
        await self.app.aclose()
        self.temp.cleanup()

    async def prompt(self, agent=None):
        return await (agent or self.agent).prompt(self.session, [text_block("Explain the task model")])

    async def test_first_prompt_and_terminal_continuation_keep_protocol_session(self):
        self.assertIs(self.app.tasks, self.app.foreground_tasks)
        response = await self.prompt()
        self.assertEqual(response.stop_reason, "end_turn")
        thread, first = await self.bridge.selection(self.session)
        self.assertEqual(thread, self.session)
        self.assertEqual(first.status, TaskStatus.COMPLETED)
        budget = await self.app.sessions.load_task_budget(first.id)
        self.assertEqual(budget.input_tokens + budget.output_tokens, 52)
        await self.prompt()
        continued, second = await self.bridge.selection(self.session)
        self.assertNotEqual(continued, self.session)
        self.assertNotEqual(second.id, first.id)
        self.assertEqual((await self.app.sessions.load_task(first.id)).status, TaskStatus.COMPLETED)
        self.assertTrue(all(item[0] == self.session for item in self.client.updates))
        reloaded = self.new_agent()
        await reloaded.load_session(str(self.root), self.session)
        self.assertEqual(await self.bridge.load_messages(self.session),
                         await self.app.sessions.load_messages(continued))
        reopened = SQLiteSessionRepository(self.product / "sessions.sqlite3")
        try:
            restored = AcpTaskService(self.app.tasks, reopened, self.root)
            recovered_thread, recovered_task = await restored.selection(self.session)
            self.assertEqual((recovered_thread, recovered_task.id), (continued, second.id))
        finally:
            reopened.close()

    async def test_paused_task_resumes_original_budget_and_contract(self):
        task = await self.app.tasks.start("Explain", thread_id=self.session)
        await self.bridge._save(self.session, task.thread_id, task.id)
        await self.app.tasks.pause(task.id)
        await self.prompt()
        _, resumed = await self.bridge.selection(self.session)
        self.assertEqual(resumed.id, task.id)
        self.assertEqual(resumed.contract, task.contract)

    async def test_cancel_is_actual_task_token_and_owner_is_released(self):
        self.reply.held = True
        running = asyncio.create_task(self.prompt())
        await self.reply.started.wait()
        _, task = await self.bridge.selection(self.session)
        self.assertIsNotNone((await self.app.tasks.recovery_checklist(task.id))["execution_owner"])
        await self.agent.cancel(self.session)
        response = await asyncio.wait_for(running, 3)
        self.assertEqual(response.stop_reason, "cancelled")
        self.assertIsNone((await self.app.tasks.recovery_checklist(task.id))["execution_owner"])

    async def test_missing_usage_preserves_unknown_reservation(self):
        self.reply.usage = False
        response = await self.prompt()
        self.assertEqual(response.stop_reason, "refusal")
        thread, task = await self.bridge.selection(self.session)
        rows = await self.app.sessions.context_records(thread, "usage")
        self.assertTrue(rows)
        self.assertTrue(any(row["status"] == "pending" and row["reserved"] > 0 for row in rows))

    async def test_projection_foreign_task_and_malformed_selection_refused(self):
        foreign = await self.app.sessions.create_thread()
        await self.app.sessions.create_task(foreign, TaskContract("Foreign", TaskAuthorization(self.temp.name)))
        with self.assertRaisesRegex(ValueError, "another project"):
            await self.bridge.selection(foreign)
        await self.app.sessions.create_checkpoint(self.session, "acp-session-selection", {
            "session_id": self.session, "thread_id": foreign, "task_id": None,
            "source_root": str(self.root)})
        with self.assertRaisesRegex(ValueError, "outside"):
            await self.bridge.load_messages(self.session)
        self.assertEqual(self.reply.calls, 0)

    async def test_unknown_legacy_history_does_not_guess_project(self):
        legacy = await self.app.sessions.create_thread()
        with self.assertRaisesRegex(ValueError, "provenance"):
            await self.bridge.selection(legacy)

    async def test_unknown_action_recovery_blocks_provider_on_original_task(self):
        task = await self.app.tasks.start("Explain", thread_id=self.session)
        await self.bridge._save(self.session, task.thread_id, task.id)
        await self.app.sessions.transition_task(task.id, TaskStatus.RUNNING)
        await self.app.sessions.append_message(task.thread_id, Message("assistant", tool_calls=(
            ToolCall("unknown-read", "read_file", {"path": "a.txt"}),)))
        await self.app.sessions.transition_task(task.id, TaskStatus.INTERRUPTED)
        response = await self.prompt()
        self.assertEqual(response.stop_reason, "refusal")
        self.assertEqual(self.reply.calls, 0)
        self.assertEqual((await self.app.sessions.load_task(task.id)).status, TaskStatus.WAITING_DECISION)

    async def test_child_budget_projection_cannot_select_main_session(self):
        task = await self.app.tasks.start("Explain", thread_id=self.session)
        await self.bridge._save(self.session, task.thread_id, task.id)
        await self.app.sessions.begin_task_execution(task.id, "test-owner", 123, 45)
        child = await self.app.sessions.create_thread(parent_thread_id=self.session)
        await self.app.sessions.bind_child_budget(child, self.session, task.id, "delegate",
            max_total_tokens=1000, max_tool_calls=1)
        await self.bridge._save(self.session, child, None)
        with self.assertRaisesRegex(ValueError, "child"):
            await self.bridge.selection(self.session)
        await self.app.sessions.release_task_execution(task.id, "test-owner")

    async def test_public_serve_uses_real_application_task_service(self):
        with patch("chaos_agent.acp_adapter.run_agent", new=AsyncMock()) as transport:
            await serve_acp(self.app)
        agent = transport.await_args.args[0]
        agent.on_connect(self.client)
        session = (await agent.new_session(str(self.root))).session_id
        response = await agent.prompt(session, [text_block("Explain the task model")])
        self.assertEqual(response.stop_reason, "end_turn")
        self.assertIs(agent._controller.tasks, self.app.tasks)
        self.assertEqual((await self.app.sessions.load_task_for_thread(session)).status, TaskStatus.COMPLETED)

    async def test_cli_empty_legacy_thread_gets_task_but_unowned_history_refused(self):
        empty = await self.app.sessions.create_thread()
        _ = [event async for event in self.app.tasks.resume_thread(empty, "Explain the task model")]
        self.assertIsNotNone(await self.app.sessions.load_task_for_thread(empty))
        old = await self.app.sessions.create_thread()
        await self.app.sessions.append_message(old, Message("user", "history without project provenance"))
        before = self.reply.calls
        with self.assertRaisesRegex(RuntimeError, "workspace-owned"):
            _ = [event async for event in self.app.tasks.resume_thread(old, "continue")]
        self.assertEqual(self.reply.calls, before)

    async def test_sdk_request_task_cancel_settles_durable_task(self):
        self.reply.held = True
        running = asyncio.create_task(self.prompt())
        await self.reply.started.wait()
        _, task = await self.bridge.selection(self.session)
        running.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await running
        self.assertEqual((await self.app.sessions.load_task(task.id)).status, TaskStatus.INTERRUPTED)
        self.assertIsNone((await self.app.tasks.recovery_checklist(task.id))["execution_owner"])

    async def test_late_cancel_preserves_completed_task(self):
        await self.prompt()
        _, task = await self.bridge.selection(self.session)
        await self.agent.cancel(self.session)
        self.assertEqual((await self.app.sessions.load_task(task.id)).status, TaskStatus.COMPLETED)

    async def test_cancel_while_completed_result_queued_preserves_durable_delivery(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class HeldClient(Client):
            async def session_update(self, session_id, update):
                entered.set()
                await release.wait()
                await super().session_update(session_id, update)
        self.agent.on_connect(HeldClient())
        running = asyncio.create_task(self.prompt())
        await entered.wait()
        _, task = await self.bridge.selection(self.session)
        async def completed():
            while (await self.app.sessions.load_task(task.id)).status is not TaskStatus.COMPLETED:
                await asyncio.sleep(0)
        await asyncio.wait_for(completed(), 3)
        await self.agent.cancel(self.session)
        release.set()
        response = await running
        self.assertEqual(response.stop_reason, "end_turn")
        self.assertEqual((await self.app.tasks.result(task.id)).execution_status, "completed")
