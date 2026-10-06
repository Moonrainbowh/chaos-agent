"""Durable reconciliation has one atomic CAS and never executes a tool."""
import tempfile
import unittest
from pathlib import Path

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ActionResult, Message, ToolCall
from code_agent.core.task import TaskStatus
from code_agent.core.task_state import TaskState
from code_agent.interfaces.task_controller import freeze_task_contract, authorization_for_task_mode
from code_agent.sessions.repository import SQLiteSessionRepository


class ActionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.repo = SQLiteSessionRepository(self.root / 'sessions.db')
        self.thread = await self.repo.create_thread()
        self.task = await self.repo.create_task(self.thread, freeze_task_contract('Read data', authorization_for_task_mode(str(self.root), 'code'), None))
        await self.repo.transition_task(self.task.id, TaskStatus.RUNNING)
        self.call = ToolCall('call-1', 'read_file', {'path': 'a.txt'})
        self.assistant = Message('assistant', tool_calls=(self.call,))
        await self.repo.append_message(self.thread, self.assistant)
        await self.repo.append_event(self.thread, AgentEvent(EventKind.MESSAGE_ADDED, {'message': self.assistant.to_dict()}))
        await self.repo.transition_task(self.task.id, TaskStatus.INTERRUPTED)

    async def asyncTearDown(self):
        self.repo.close()
        self.temporary.cleanup()

    async def request(self, **overrides):
        facts = await self.repo.recovery_checklist(self.task.id)
        data = dict(call_id=self.call.id, message_sequence=facts['pending_action_records'][0]['message_sequence'],
                    version=facts['recovery_version'], workspace_root=str(self.root), decision='durable_receipt',
                    reason='Review interrupted call', evidence='Persisted engine receipt', operator_authorized=True)
        data.update(overrides)
        return data

    async def receipt(self, **fields):
        await self.repo.append_event(self.thread, AgentEvent(EventKind.ACTION_REQUESTED, {'request': self.call.to_dict()}))
        result = ActionResult(self.call.id, self.call.name, {'text': 'actual data'})
        await self.repo.append_event(self.thread, AgentEvent(EventKind.ACTION_COMPLETED, {'result': result.to_dict(), **fields}))

    async def unchanged_rejection(self, **fields):
        before = await self.repo.recovery_checklist(self.task.id)
        with self.assertRaises((ValueError, PermissionError)):
            await self.repo.resolve_pending_action(self.task.id, **await self.request(**fields))
        after = await self.repo.recovery_checklist(self.task.id)
        self.assertEqual(before['recovery_version'], after['recovery_version'])
        self.assertEqual(before['unresolved_tool_calls'], after['unresolved_tool_calls'])

    async def test_receipt_closes_pair_and_duplicate_is_rejected(self):
        await self.receipt()
        request = await self.request()
        contract = (await self.repo.load_task(self.task.id)).contract
        event = await self.repo.resolve_pending_action(self.task.id, **request)
        self.assertEqual(event.kind, EventKind.ACTION_OUTCOME_RESOLVED)
        self.assertTrue(event.payload['verified'])
        self.assertEqual((await self.repo.recovery_checklist(self.task.id))['unresolved_tool_calls'], ())
        self.assertEqual((await self.repo.load_task(self.task.id)).contract, contract)
        self.assertEqual(len(await self.repo.list_verification_evidence(self.task.id)), 0)
        with self.assertRaises(ValueError):
            await self.repo.resolve_pending_action(self.task.id, **request)
        records = await self.repo.load_message_records(self.thread)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1].message.tool_call_id, self.call.id)

    async def test_stale_wrong_action_workspace_and_missing_authorization_do_not_write(self):
        await self.receipt()
        await self.unchanged_rejection(version='0'*64)
        await self.unchanged_rejection(call_id='wrong')
        await self.unchanged_rejection(message_sequence=999)
        await self.unchanged_rejection(workspace_root=str(self.root / 'other'))
        await self.unchanged_rejection(operator_authorized=False)

    async def test_unknown_command_and_user_shell_receipt_remain_blocked(self):
        await self.unchanged_rejection()
        await self.receipt(origin='user_shell')
        await self.unchanged_rejection()

    async def test_conflicting_receipts_remain_blocked(self):
        await self.receipt()
        await self.receipt()
        await self.unchanged_rejection()

    async def test_result_before_original_assistant_event_is_not_a_receipt(self):
        thread = await self.repo.create_thread()
        task = await self.repo.create_task(thread, self.task.contract)
        await self.repo.transition_task(task.id, TaskStatus.RUNNING)
        result = ActionResult(self.call.id, self.call.name, 'old result')
        await self.repo.append_event(thread, AgentEvent(EventKind.ACTION_COMPLETED, {'result': result.to_dict()}))
        await self.repo.append_message(thread, self.assistant)
        await self.repo.append_event(thread, AgentEvent(EventKind.MESSAGE_ADDED, {'message': self.assistant.to_dict()}))
        await self.repo.append_event(thread, AgentEvent(EventKind.ACTION_REQUESTED, {'request': self.call.to_dict()}))
        await self.repo.transition_task(task.id, TaskStatus.INTERRUPTED)
        facts = await self.repo.recovery_checklist(task.id)
        request = await self.request(version=facts['recovery_version'], message_sequence=facts['pending_action_records'][0]['message_sequence'])
        with self.assertRaises(ValueError):
            await self.repo.resolve_pending_action(task.id, **request)
        self.assertEqual((await self.repo.recovery_checklist(task.id))['recovery_version'], facts['recovery_version'])

    async def test_owner_release_is_instance_cas_and_dead_paused_owner_can_be_retired(self):
        await self.repo.transition_task(self.task.id, TaskStatus.RUNNING)
        await self.repo.register_task_execution(self.task.id, 'old-instance', 123, 1.0)
        self.assertTrue(await self.repo.release_task_execution(self.task.id, 'old-instance'))
        await self.repo.register_task_execution(self.task.id, 'new-instance', 123, 1.0)
        self.assertFalse(await self.repo.release_task_execution(self.task.id, 'old-instance'))
        self.assertEqual((await self.repo.recovery_checklist(self.task.id))['execution_owner']['instance_id'], 'new-instance')
        await self.repo.transition_task(self.task.id, TaskStatus.PAUSED)
        request = await self.request(decision='operator_not_executed')
        with self.assertRaisesRegex(ValueError, 'owner'):
            await self.repo.resolve_pending_action(self.task.id, **request, owner_alive=lambda *_: True)
        await self.repo.resolve_pending_action(self.task.id, **request, owner_alive=lambda *_: False)
        self.assertIsNone((await self.repo.recovery_checklist(self.task.id))['execution_owner'])
        self.assertEqual((await self.repo.load_task(self.task.id)).status, TaskStatus.PAUSED)

    async def test_operator_report_is_persisted_without_success_or_verification(self):
        await self.repo.save_task_state(self.thread, TaskState(code_generation=3, subject_hash='old-subject', verified_facts=('Old verification',)))
        event = await self.repo.resolve_pending_action(self.task.id, **await self.request(decision='operator_not_executed', evidence='Operator inspected external command log'))
        self.assertFalse(event.payload['verified'])
        messages = await self.repo.load_messages(self.thread)
        self.assertIn('"is_error": true', messages[-1].content)
        self.assertIn('operator_report', messages[-1].content)
        self.assertEqual(len(await self.repo.list_verification_evidence(self.task.id)), 0)
        state = await self.repo.load_task_state(self.thread)
        self.assertEqual(state.code_generation, 4)
        self.assertEqual(state.subject_hash, '')
        self.assertEqual(state.verified_facts, ())

    async def test_wrong_task_same_call_id_cannot_consume_other_thread_receipt(self):
        await self.receipt()
        other = await self.repo.create_thread()
        task = await self.repo.create_task(other, self.task.contract)
        await self.repo.append_message(other, self.assistant)
        request = await self.request()
        with self.assertRaises(ValueError):
            await self.repo.resolve_pending_action(task.id, **request)
        self.assertEqual(len((await self.repo.recovery_checklist(self.task.id))['unresolved_tool_calls']), 1)
