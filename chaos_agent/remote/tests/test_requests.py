import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import Message, ToolCall
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.interfaces.approval import ApprovalBroker, ApprovalRequest
from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.sessions.repository import SQLiteSessionRepository
from chaos_agent.remote.task_controller import RemoteTaskController
from chaos_agent.remote.requests import RemoteRequestControl
from chaos_agent.remote.errors import RemoteConflict, RemoteInputError, RemoteNotFound


class Foreground(ForegroundTaskController):
    def __init__(self, sessions, root):
        super().__init__(None, sessions, root)
        self.continued = 0

    async def restore_runtime_settings(self, task_id):
        pass

    async def events(self, task_id, prompt=None):
        self.continued += 1
        await self._sessions.transition_task(task_id, TaskStatus.RUNNING)
        yield AgentEvent(EventKind.TASK_STATUS_CHANGED, {'status': 'running'})
        await self._sessions.transition_task(task_id, TaskStatus.PAUSED)
        yield AgentEvent(EventKind.TASK_PAUSED, {'status': 'paused'})


class RequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / 'sessions.sqlite3'
        assert self.path.is_absolute() and self.path.parent == self.root
        self.repo = SQLiteSessionRepository(self.path)
        self.thread = await self.repo.create_thread()
        self.task = await self.repo.create_task(self.thread,
            TaskContract('test task', TaskAuthorization.local_workspace(str(self.root))))
        await self.repo.transition_task(self.task.id, TaskStatus.RUNNING)
        await self.repo.transition_task(self.task.id, TaskStatus.WAITING_DECISION)
        self.foreground = Foreground(self.repo, self.root)
        self.broker = ApprovalBroker(self.repo)
        self.app = SimpleNamespace(workspace_root=self.root, sessions=self.repo,
            foreground_tasks=self.foreground, approvals=self.broker)
        self.tasks = RemoteTaskController(self.app)
        self.control = RemoteRequestControl(self.tasks)

    async def asyncTearDown(self):
        await self.tasks.aclose()
        self.repo.close()
        self.temp.cleanup()

    async def card(self, operation):
        snapshot = await self.control.pending(self.thread)
        return next(card for card in snapshot['requests'] if card['kind'] == 'decision'
                    and card['status'] == 'pending' and card['preview']['operation'] == operation)

    def answer(self, card, **extra):
        return {**{key: card[key] for key in ('request_id', 'action_digest', 'state_version', 'owner_instance_id')},
                'approved': True, **extra}

    async def test_query_is_stable_and_bound_choice_cannot_be_replaced(self):
        first = await self.control.pending(self.thread)
        second = await self.control.pending(self.thread)
        self.assertEqual({card['request_id'] for card in first['requests']},
                         {card['request_id'] for card in second['requests']})
        card = await self.card('continue')
        with self.assertRaises(RemoteInputError):
            await self.control.respond(self.task.id, self.answer(card, operation='accept_partial'), lambda: True)
        with self.assertRaises(RemoteNotFound):
            await self.control.respond('wrong', self.answer(card), lambda: True)
        with self.assertRaises(PermissionError):
            await self.control.respond(self.task.id, self.answer(card), lambda: False)

    async def test_accept_partial_commits_once_and_preserves_unverified_result(self):
        card = await self.card('accept_partial')
        reply = await self.control.respond(self.task.id, self.answer(card), lambda: True)
        self.assertTrue(reply['consumed_now'])
        self.assertEqual((await self.repo.load_task(self.task.id)).status, TaskStatus.ACCEPTED_PARTIAL)
        result = await self.foreground.result(self.task.id)
        self.assertEqual(result.execution_status, 'accepted_partial')
        self.assertNotEqual(result.verification_status, 'verified')
        # History changed because a result was persisted: the old card cannot unlock a new action.
        with self.assertRaises(ValueError):
            await self.control.respond(self.task.id, self.answer(card), lambda: True)

    async def test_continue_uses_same_task_and_single_slot(self):
        card = await self.card('continue')
        reply = await self.control.respond(self.task.id, self.answer(card), lambda: True)
        self.assertEqual(reply['continuation']['task_id'], self.task.id)
        await self.tasks._active.runner
        self.assertEqual(self.foreground.continued, 1)
        with self.assertRaises(ValueError):
            await self.control.respond(self.task.id, self.answer(card), lambda: True)

    async def test_stop_and_deny_do_not_run_model(self):
        card = await self.card('stop')
        reply = await self.control.respond(self.task.id, self.answer(card, approved=False), lambda: True)
        self.assertEqual(reply['status'], 'denied')
        card = await self.card('stop')
        await self.control.respond(self.task.id, self.answer(card), lambda: True)
        self.assertEqual((await self.repo.load_task(self.task.id)).status, TaskStatus.FAILED)
        self.assertEqual(self.foreground.continued, 0)

    async def test_unknown_actions_block_continue_partial_and_reconcile_without_execution(self):
        await self.repo.append_message(self.thread, Message('assistant', tool_calls=(
            ToolCall('unknown', 'run_command', {'command': 'do not execute'}),)))
        snapshot = await self.control.pending(self.thread)
        operations = [card['preview']['operation'] for card in snapshot['requests'] if card['status'] == 'pending']
        self.assertNotIn('continue', operations)
        self.assertNotIn('accept_partial', operations)
        card = next(card for card in snapshot['requests'] if card['preview'].get('reconciliation', {}).get('decision') == 'operator_not_executed')
        with self.assertRaises(RemoteInputError):
            await self.control.respond(self.task.id, self.answer(card), lambda: True)
        await self.control.respond(self.task.id, self.answer(card, reason='Checked outside tool', evidence='Explicit operator report'), lambda: True)
        facts = await self.foreground.recovery_checklist(self.task.id)
        self.assertEqual(facts['unresolved_tool_calls'], ())
        self.assertEqual(self.foreground.continued, 0)
        records = await self.repo.load_message_records(self.thread)
        self.assertIn('"verified": false', records[-1].message.content)

    async def test_late_unknown_action_invalidates_previously_shown_continue(self):
        card = await self.card('continue')
        await self.repo.append_message(self.thread, Message('assistant', tool_calls=(ToolCall('unknown', 'write_file', {'path': 'x', 'content': 'x'}),)))
        with self.assertRaises(RemoteConflict):
            await self.control.respond(self.task.id, self.answer(card), lambda: True)
        self.assertEqual(self.foreground.continued, 0)

    async def test_unknown_reconciliation_can_be_denied_without_operator_report(self):
        await self.repo.append_message(self.thread, Message('assistant', tool_calls=(
            ToolCall('unknown', 'run_command', {'command': 'do not execute'}),)))
        card = await self.card('reconcile')
        result = await self.control.respond(self.task.id, self.answer(card, approved=False), lambda: True)
        self.assertEqual(result['status'], 'denied')
        self.assertEqual(len((await self.foreground.recovery_checklist(self.task.id))['unresolved_tool_calls']), 1)

    async def test_partial_projection_refuses_uncommitted_or_wrong_stamp(self):
        prior = await self.foreground.result(self.task.id)
        with self.assertRaises(ValueError):
            await self.foreground.record_accepted_partial_result(self.task.id, '2000-01-01T00:00:00Z', prior)
        card = await self.card('accept_partial')
        await self.control.respond(self.task.id, self.answer(card), lambda: True)
        with self.assertRaises(ValueError):
            await self.foreground.record_accepted_partial_result(self.task.id, '2000-01-01T00:00:00Z', prior)

    async def test_remote_approval_reuses_central_live_broker(self):
        await self.repo.transition_task(self.task.id, TaskStatus.RUNNING)
        await self.repo.register_task_execution(self.task.id, 'owner', 12, 34.0)
        waiting = asyncio.create_task(self.broker.request(ApprovalRequest('call', 'write_file', {'path': 'x'}), CancellationToken(),
            execution_context=ActionExecutionContext(self.thread, self.thread, 'call', self.task.id), workspace_root=str(self.root)))
        await self.broker.next_request()
        card = (await self.control.pending(self.thread))['requests'][0]
        await self.control.respond(self.task.id, self.answer(card), lambda: True)
        self.assertTrue(await waiting)

    async def test_lookup_cannot_close_active_other_project(self):
        other = self.root / 'other'
        other.mkdir()
        thread = await self.repo.create_thread()
        task = await self.repo.create_task(thread, TaskContract('other', TaskAuthorization.local_workspace(str(other))))
        blocker = asyncio.create_task(asyncio.Event().wait())
        self.tasks._active = SimpleNamespace(task_id=self.task.id, session_id=self.thread,
            application=self.app, runner=blocker)
        try:
            with self.assertRaises(RemoteConflict):
                await self.tasks.request_application(task.id)
        finally:
            self.tasks._active = None
            blocker.cancel()
            await asyncio.gather(blocker, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
