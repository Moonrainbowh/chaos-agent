"""Finished stream projections stay subordinate to their durable task identity."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.sessions.repository import SQLiteSessionRepository
from chaos_agent.remote.task_controller import RemoteTaskController, _RemoteRun


class StatusReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='remote-status-')
        self.root = Path(self.temp.name).resolve()
        database = self.root/'sessions.sqlite3'
        assert database.is_absolute() and database.resolve().is_relative_to(self.root)
        self.repo = SQLiteSessionRepository(database)
        self.foreground = ForegroundTaskController(None, self.repo, self.root)
        self.app = SimpleNamespace(workspace_root=self.root,sessions=self.repo,foreground_tasks=self.foreground)
        self.controller = RemoteTaskController(self.app)
        self.thread = await self.repo.create_thread()
        self.task = await self.repo.create_task(self.thread,
            TaskContract('isolated fixed task',TaskAuthorization.local_workspace(str(self.root))))
        await self.repo.transition_task(self.task.id,TaskStatus.RUNNING)
        await self.repo.transition_task(self.task.id,TaskStatus.WAITING_DECISION)
        self.run = _RemoteRun(self.task.id,self.thread,self.app,'fixed fixture',
            status='waiting_decision',stream_done=True)
        self.controller._runs[self.thread] = self.run
        self.controller._active = self.run

    async def asyncTearDown(self):
        await self.controller.aclose()
        self.repo.close()
        self.temp.cleanup()

    async def test_finished_projection_reads_durable_terminal_after_lost_response(self):
        await self.repo.transition_task(self.task.id,TaskStatus.ACCEPTED_PARTIAL)
        snapshot = await self.controller.status()
        self.assertEqual(snapshot['status'],'accepted_partial')
        self.assertEqual(snapshot['result']['execution_status'],'accepted_partial')
        self.assertNotEqual(snapshot['result']['verification_status'],'verified')

    async def test_same_task_replacement_run_is_not_overwritten_after_await(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.foreground.result
        async def delayed(task_id):
            entered.set()
            await release.wait()
            return await original(task_id)
        self.foreground.result = delayed
        await self.repo.transition_task(self.task.id,TaskStatus.ACCEPTED_PARTIAL)
        pending = asyncio.create_task(self.controller.status())
        await entered.wait()
        newer = _RemoteRun(self.task.id,self.thread,self.app,'fixed replacement',status='running')
        self.controller._runs[self.thread] = newer
        self.controller._active = newer
        release.set()
        snapshot = await pending
        self.assertEqual(snapshot['status'],'running')
        self.assertEqual(newer.status,'running')
        self.assertIsNone(newer.result)
        self.assertEqual(self.run.status,'waiting_decision')

    async def test_other_session_history_does_not_replace_active_run(self):
        await self.repo.transition_task(self.task.id,TaskStatus.ACCEPTED_PARTIAL)
        other_thread = await self.repo.create_thread()
        other = await self.repo.create_task(other_thread,self.task.contract)
        await self.repo.transition_task(other.id,TaskStatus.RUNNING)
        active = _RemoteRun(other.id,other_thread,self.app,'fixed other',status='running')
        self.controller._active = active
        self.controller._runs[other_thread] = active
        history = await self.controller.history(self.thread,before=None,limit=50)
        self.assertEqual(history['task']['status'],'accepted_partial')
        self.assertEqual(history['result']['execution_status'],'accepted_partial')
        self.assertEqual((await self.controller.status())['task_id'],other.id)
        self.assertEqual(active.status,'running')

    async def test_thread_mismatch_does_not_read_or_copy_other_task_result(self):
        wrong = _RemoteRun(self.task.id,'unrelated-session',self.app,'fixed mismatch',
            status='waiting_decision',stream_done=True)
        self.controller._runs[wrong.session_id] = wrong
        self.controller._active = wrong
        await self.repo.transition_task(self.task.id,TaskStatus.ACCEPTED_PARTIAL)
        self.assertEqual((await self.controller.status())['status'],'waiting_decision')
        self.assertIsNone(wrong.result)

    async def test_history_without_any_old_run_reads_persisted_partial_result(self):
        await self.repo.transition_task(self.task.id,TaskStatus.ACCEPTED_PARTIAL)
        self.controller._runs.clear()
        self.controller._active = None
        history = await self.controller.history(self.thread,before=None,limit=50)
        self.assertEqual(history['task']['status'],'accepted_partial')
        self.assertEqual(history['result']['execution_status'],'accepted_partial')

    async def test_history_replaces_finished_run_during_result_read(self):
        self.run.result = (await self.foreground.result(self.task.id)).to_dict()
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.foreground.result
        async def delayed(task_id):
            entered.set()
            await release.wait()
            return await original(task_id)
        self.foreground.result = delayed
        pending = asyncio.create_task(self.controller.history(self.thread,before=None,limit=50))
        await entered.wait()
        await self.repo.transition_task(self.task.id,TaskStatus.RUNNING)
        newer = _RemoteRun(self.task.id,self.thread,self.app,'fixed resumed',status='running')
        self.controller._runs[self.thread] = newer
        self.controller._active = newer
        release.set()
        history = await asyncio.wait_for(pending,5)
        self.assertEqual(history['task']['status'],'running')
        self.assertIsNone(history['result'])
        self.assertEqual(history['active_task']['id'],self.task.id)

    async def test_history_replaces_run_during_message_page_read(self):
        self.run.result = (await self.foreground.result(self.task.id)).to_dict()
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.controller.catalog.message_page
        calls = 0
        async def delayed(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
            return await original(*args,**kwargs)
        self.controller.catalog.message_page = delayed
        pending = asyncio.create_task(self.controller.history(self.thread,before=10,limit=50))
        await entered.wait()
        await self.repo.transition_task(self.task.id,TaskStatus.RUNNING)
        newer = _RemoteRun(self.task.id,self.thread,self.app,'fixed page resumed',status='running',
            base_message_sequence=5,sequence=2)
        self.controller._runs[self.thread] = newer
        self.controller._active = newer
        release.set()
        history = await asyncio.wait_for(pending,5)
        self.assertEqual(history['task']['status'],'running')
        self.assertIsNone(history['result'])
        self.assertEqual(history['active_task']['id'],self.task.id)
        self.assertEqual(history['event_sequence'],2)
        self.assertGreaterEqual(calls,2)

    async def test_history_without_run_resumes_during_durable_result_read(self):
        self.controller._runs.clear()
        self.controller._active = None
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.foreground.result
        async def delayed(task_id):
            entered.set()
            await release.wait()
            return await original(task_id)
        self.foreground.result = delayed
        pending = asyncio.create_task(self.controller.history(self.thread,before=None,limit=50))
        await entered.wait()
        await self.repo.transition_task(self.task.id,TaskStatus.RUNNING)
        newer = _RemoteRun(self.task.id,self.thread,self.app,'fixed resumed without old run',status='running')
        self.controller._runs[self.thread] = newer
        self.controller._active = newer
        release.set()
        history = await asyncio.wait_for(pending,5)
        self.assertEqual(history['task']['status'],'running')
        self.assertEqual(history['active_task']['id'],self.task.id)
        self.assertIsNone(history['result'])

    async def test_old_finished_run_does_not_attach_result_to_externally_active_task(self):
        self.run.result = (await self.foreground.result(self.task.id)).to_dict()
        await self.repo.transition_task(self.task.id,TaskStatus.RUNNING)
        history = await self.controller.history(self.thread,before=None,limit=50)
        self.assertEqual(history['task']['status'],'running')
        self.assertIsNone(history['result'])
        self.assertIsNone(history['active_task'])
