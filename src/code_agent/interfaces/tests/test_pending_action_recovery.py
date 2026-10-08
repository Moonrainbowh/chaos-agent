"""Repeated and cross-entry resumes cannot bypass an unmatched durable action."""
import tempfile
import asyncio
import unittest
from pathlib import Path
from code_agent.core.models import Message, ToolCall
from code_agent.core.events import EventKind
from code_agent.core.events import AgentEvent
from code_agent.core.models import ActionResult
from code_agent.interfaces.commands import parse_command, execute_command
from code_agent.core.task import TaskStatus
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.sessions.repository import SQLiteSessionRepository


class CountingRunner:
    def __init__(self):
        self.calls = 0
    async def run(self, *args, **kwargs):
        self.calls += 1
        if False:
            yield None


class PendingActionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_does_not_allow_reconciliation_until_runner_releases_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repo = SQLiteSessionRepository(root / 'sessions.sqlite3')
            entered, finish = asyncio.Event(), asyncio.Event()
            class HeldRunner:
                async def run(self, prompt, **kwargs):
                    await repo.append_message(kwargs['thread_id'], Message('assistant', tool_calls=(ToolCall('in-flight', 'run_command', {'command': 'external operation'}),)))
                    entered.set()
                    await finish.wait()
                    if False:
                        yield None
            controller = ForegroundTaskController(AgentController(HeldRunner()), repo, root)
            task = await controller.start('Execute operation')
            async def consume():
                async for _ in controller.events(task.id):
                    pass
            worker = asyncio.create_task(consume())
            await entered.wait()
            await controller.pause(task.id)
            facts = await controller.recovery_checklist(task.id)
            decision = dict(call_id='in-flight', message_sequence=facts['pending_action_records'][0]['message_sequence'],
                version=facts['recovery_version'], decision='operator_not_executed', reason='Inspect interrupted command',
                evidence='Operator report', operator_authorized=True)
            try:
                with self.assertRaisesRegex(ValueError, 'owner'):
                    await controller.resolve_pending_action(task.id, **decision)
                self.assertEqual((await controller.recovery_checklist(task.id))['recovery_version'], facts['recovery_version'])
            finally:
                finish.set()
                await worker
            facts = await controller.recovery_checklist(task.id)
            self.assertIsNone(facts['execution_owner'])
            with self.assertRaisesRegex(ValueError, 'stale'):
                await controller.resolve_pending_action(task.id, **decision)
            decision['version'] = facts['recovery_version']
            await controller.resolve_pending_action(task.id, **decision)
            self.assertEqual((await controller.recovery_checklist(task.id))['unresolved_tool_calls'], ())

    async def test_operator_cli_reconciles_receipt_then_can_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repo = SQLiteSessionRepository(root / 'sessions.sqlite3')
            runner = CountingRunner()
            controller = ForegroundTaskController(AgentController(runner), repo, root)
            task = await controller.start('Read data')
            await repo.transition_task(task.id, TaskStatus.RUNNING)
            call = ToolCall('known-read', 'read_file', {'path': 'a.txt'})
            assistant = Message('assistant', tool_calls=(call,))
            await repo.append_message(task.thread_id, assistant)
            await repo.append_event(task.thread_id, AgentEvent(EventKind.MESSAGE_ADDED, {'message': assistant.to_dict()}))
            await repo.append_event(task.thread_id, AgentEvent(EventKind.ACTION_REQUESTED, {'request': call.to_dict()}))
            await repo.append_event(task.thread_id, AgentEvent(EventKind.ACTION_COMPLETED, {'result': ActionResult(call.id, call.name, 'data').to_dict()}))
            await repo.transition_task(task.id, TaskStatus.INTERRUPTED)
            facts = await controller.recovery_checklist(task.id)
            record = facts['pending_action_records'][0]
            output = []
            command = parse_command(('task', 'resolve', task.id, call.id, str(record['message_sequence']), facts['recovery_version'], 'durable_receipt', 'Inspect original receipt', 'Persisted action event'))
            self.assertEqual(await execute_command(command, controller._controller, None, output.append, controller), 0)
            self.assertEqual(runner.calls, 0)
            self.assertIn('action_outcome_resolved', output[0])
            self.assertEqual((await repo.load_task(task.id)).contract, task.contract)
            async for _ in controller.resume(task.id):
                pass
            self.assertEqual(runner.calls, 1)

    async def test_repeated_reopened_and_cross_entry_resumes_remain_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / 'sessions.sqlite3'
            repo = SQLiteSessionRepository(path)
            runner = CountingRunner()
            controller = ForegroundTaskController(AgentController(runner), repo, root)
            task = await controller.start('Update file')
            await repo.transition_task(task.id, TaskStatus.RUNNING)
            await repo.append_message(task.thread_id, Message('assistant', tool_calls=(
                ToolCall('unknown-write', 'write_file', {'path': 'file.txt', 'content': 'new'}),)))
            await repo.transition_task(task.id, TaskStatus.INTERRUPTED)
            for entry in ('resume', 'events', 'resume_thread', 'resume'):
                repo = SQLiteSessionRepository(path)
                controller = ForegroundTaskController(AgentController(runner), repo, root)
                identifier = task.thread_id if entry == 'resume_thread' else task.id
                events = [event async for event in getattr(controller, entry)(identifier)]
                self.assertEqual(runner.calls, 0, f'{entry} bypassed durable uncertainty')
                self.assertEqual((await repo.load_task(task.id)).status, TaskStatus.WAITING_DECISION)
                self.assertEqual(events[0].kind, EventKind.TASK_DECISION_REQUIRED)
                self.assertEqual(len((await repo.recovery_checklist(task.id))['unresolved_tool_calls']), 1)
