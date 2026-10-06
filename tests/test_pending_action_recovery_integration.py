"""Default Host reconciles local writes using physical facts without replay."""
import unittest
from unittest.mock import patch

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import Message, ToolCall, ActionRequest
from code_agent.core.task import TaskStatus
from code_agent.core.task_state import TaskState
from tests.test_tui_repair_integration import application_fixture


class PendingActionRecoveryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def prepare(self, app):
        task = await app.foreground_tasks.start('Write recovered.txt')
        await app.sessions.transition_task(task.id, TaskStatus.RUNNING)
        call = ToolCall('interrupted-write', 'write_file', {'path': 'recovered.txt', 'content': 'trusted content'})
        assistant = Message('assistant', tool_calls=(call,))
        await app.sessions.append_message(task.thread_id, assistant)
        await app.sessions.append_event(task.thread_id, AgentEvent(EventKind.MESSAGE_ADDED, {'message': assistant.to_dict()}))
        request = ActionRequest(call.id, call.name, call.arguments)
        plan = app.dispatcher.editor.plan_write('recovered.txt', 'trusted content')
        context = ActionExecutionContext(task.thread_id, task.thread_id, call.id, task.id)
        await app.dispatcher.capture.apply_edit(context, request, plan)
        await app.sessions.save_task_state(task.thread_id, TaskState(code_generation=7,
            subject_hash='old-subject', verified_facts=('Previously verified',)))
        await app.sessions.transition_task(task.id, TaskStatus.INTERRUPTED)
        facts = await app.foreground_tasks.recovery_checklist(task.id)
        record = facts['pending_action_records'][0]
        decision = dict(call_id=call.id, message_sequence=record['message_sequence'], version=facts['recovery_version'],
                        decision='local_mutation', reason='Inspect completed mutation', evidence='Host physical hash and workspace identity', operator_authorized=True)
        return task, decision

    async def test_completed_mutation_is_verified_without_replaying_write(self):
        with application_fixture() as app:
            try:
                task, decision = await self.prepare(app)
                contract = (await app.sessions.load_task(task.id)).contract
                budget = await app.sessions.load_task_budget(task.id)
                with patch.object(app.dispatcher.capture, 'apply_edit', side_effect=AssertionError('must not replay')):
                    event = await app.foreground_tasks.resolve_pending_action(task.id, **decision)
                self.assertTrue(event.payload['verified'])
                self.assertEqual((await app.foreground_tasks.recovery_checklist(task.id))['unresolved_tool_calls'], ())
                self.assertEqual((await app.sessions.load_task(task.id)).contract, contract)
                self.assertEqual(await app.sessions.load_task_budget(task.id), budget)
                self.assertEqual((app.dispatcher.editor.guard.root / 'recovered.txt').read_text(), 'trusted content')
                self.assertEqual(await app.sessions.list_verification_evidence(task.id), ())
                state = await app.sessions.load_task_state(task.thread_id)
                self.assertEqual(state.code_generation, 8)
                self.assertEqual(state.subject_hash, '')
                self.assertIn('recovered.txt', state.files_changed)
                self.assertEqual(state.verified_facts, ())
            finally:
                await app.aclose()

    async def test_external_change_cannot_be_guessed_success_from_old_receipt(self):
        with application_fixture() as app:
            try:
                task, decision = await self.prepare(app)
                (app.dispatcher.editor.guard.root / 'recovered.txt').write_text('user change')
                with self.assertRaisesRegex(ValueError, 'contents'):
                    await app.foreground_tasks.resolve_pending_action(task.id, **decision)
                self.assertEqual(len((await app.foreground_tasks.recovery_checklist(task.id))['unresolved_tool_calls']), 1)
                self.assertEqual((app.dispatcher.editor.guard.root / 'recovered.txt').read_text(), 'user change')
            finally:
                await app.aclose()

    async def test_same_path_replacement_with_same_content_is_foreign(self):
        with application_fixture() as app:
            try:
                task, decision = await self.prepare(app)
                root = app.dispatcher.editor.guard.root
                original = root.with_name('original-workspace')
                root.rename(original)
                root.mkdir()
                (root / 'recovered.txt').write_text('trusted content')
                with self.assertRaisesRegex(ValueError, 'identity'):
                    await app.foreground_tasks.resolve_pending_action(task.id, **decision)
                self.assertEqual(len((await app.foreground_tasks.recovery_checklist(task.id))['unresolved_tool_calls']), 1)
                self.assertEqual((original / 'recovered.txt').read_text(), 'trusted content')
                self.assertEqual((root / 'recovered.txt').read_text(), 'trusted content')
            finally:
                await app.aclose()
