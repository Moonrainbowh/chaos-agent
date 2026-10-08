"""Real durable child mutations expire parent evidence without promoting claims."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from code_agent.core.limits import EngineLimits
from code_agent.core.models import ActionRequest, ActionResult
from code_agent.core.task import TaskStatus, TaskContract, TaskAuthorization
from code_agent.sessions.repository import SQLiteSessionRepository


class ChildActionStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = SQLiteSessionRepository(Path(self.temp.name) / 'sessions.sqlite3')
        self.owner = await self.repo.create_thread()
        self.task = await self.repo.create_task(self.owner, TaskContract('modify', TaskAuthorization.local_workspace(self.temp.name)))
        await self.repo.transition_task(self.task.id, TaskStatus.RUNNING)
        await self.repo.register_task_execution(self.task.id, 'owner', 123, 45)
        await self.repo.get_or_create_task_budget(self.owner, 'model', EngineLimits())

    async def asyncTearDown(self):
        self.repo.close()
        self.temp.cleanup()

    async def child(self, request):
        child = await self.repo.create_thread(parent_thread_id=self.owner)
        await self.repo.bind_child_budget(child, self.owner, self.task.id, request,
            max_total_tokens=800, max_tool_calls=2)
        return child
    async def action(self, child, id='write', name='write_file', *, error=False, output=None, metadata=None):
        request = ActionRequest(id, name, {'path': id + '.py'})
        result = ActionResult(id, name, output or {}, is_error=error, metadata=metadata or {})
        return await self.repo.reduce_task_state(child, request, result)

    async def test_actual_mutation_projection_and_duplicate_receipt(self):
        child = await self.child('delegate')
        original = await self.repo.load_task_state(self.owner)
        child_state = await self.action(child)
        state = await self.repo.load_task_state(self.owner)
        self.assertEqual(state.files_changed, child_state.files_changed)
        self.assertEqual(state.code_generation, original.code_generation + 1)
        self.assertEqual(state.subject_hash, '')
        await self.action(child)
        self.assertEqual(await self.repo.load_task_state(self.owner), state)
        with self.assertRaises(ValueError):
            await self.action(child, output={'changed': 'different result'})
        with self.assertRaises(ValueError):
            await self.repo.save_task_state(self.owner, original)

    async def test_concurrent_children_merge_paths_and_generation(self):
        first, second = await self.child('a'), await self.child('b')
        await asyncio.gather(self.action(first, 'one'), self.action(second, 'two'))
        state = await self.repo.load_task_state(self.owner)
        self.assertEqual(set(state.files_changed), {'one.py', 'two.py'})
        self.assertEqual(state.code_generation, 2)
        with self.assertRaises(ValueError):
            await self.repo.save_task_state(self.owner, replace(state, files_changed=('one.py',)))

    async def test_partial_failed_edit_expires_subject(self):
        child = await self.child('delegate')
        await self.action(child, name='apply_workspace_edit_plan_v1', error=True,
            output={'workspace_may_have_changed': True, 'paths': ['part.py']})
        state = await self.repo.load_task_state(self.owner)
        self.assertEqual(state.files_changed, ('part.py',))
        self.assertEqual(state.code_generation, 1)
        self.assertIn('Workspace may have changed: part.py', state.working_notes)

    async def test_attempted_command_expires_but_preflight_and_advice_do_not(self):
        child = await self.child('delegate')
        await self.action(child, name='run_process_v1', error=True)
        await self.action(child, 'advice', name='run_verification', output={'passed': True})
        self.assertEqual((await self.repo.load_task_state(self.owner)).code_generation, 0)
        await self.action(child, 'command', name='run_process_v1', error=True,
            metadata={'execution_attempted': True})
        state = await self.repo.load_task_state(self.owner)
        self.assertEqual(state.code_generation, 1)
        self.assertEqual(len(state.failed_commands), 1)
        self.assertEqual(await self.repo.list_completed_verification_evidence(self.task.id), ())

    async def test_paused_current_owner_can_settle_but_replaced_owner_cannot(self):
        child = await self.child('delegate')
        await self.repo.transition_task(self.task.id, TaskStatus.PAUSED)
        await self.action(child, 'settle')
        await self.repo.release_task_execution(self.task.id, 'owner')
        await self.repo.begin_task_execution(self.task.id, 'new-owner', 123, 45)
        state = await self.repo.load_task_state(self.owner)
        with self.assertRaises(ValueError):
            await self.action(child, 'stale')
        self.assertEqual(await self.repo.load_task_state(self.owner), state)

    async def test_partial_write_error_is_not_reported_as_successful_edit(self):
        child = await self.child('delegate')
        await self.action(child, 'partial', error=True, metadata={'workspace_may_have_changed': True})
        state = await self.repo.load_task_state(self.owner)
        self.assertEqual(state.files_changed, ('partial.py',))
        self.assertEqual(state.code_generation, 1)
        self.assertNotIn('Changed file: partial.py', state.verified_facts)

    async def test_readonly_binding_does_not_guard_unrelated_state_saves(self):
        await self.child('delegate')
        state = await self.repo.load_task_state(self.owner)
        await self.repo.save_task_state(self.owner, replace(state, code_generation=3))
        await self.repo.save_task_state(self.owner, state)

    async def test_snapshot_cas_rejects_equal_generation_changed_state(self):
        child = await self.child('delegate')
        expected = await self.repo.load_task_state(self.owner)
        await self.action(child, 'same')
        current = await self.repo.load_task_state(self.owner)
        with self.assertRaisesRegex(ValueError, 'changed during subject snapshot'):
            await self.repo.save_task_state_if_current(self.owner,
                replace(current, subject_hash='stale-hash'), expected)
        self.assertEqual(await self.repo.load_task_state(self.owner), current)
        await self.repo.save_task_state_if_current(self.owner,
            replace(current, subject_hash='fresh-hash'), current)
