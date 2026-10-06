"""Execution registration preserves durable ownership across connections."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone

from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.sessions.repository import SQLiteSessionRepository


class TaskExecutionOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'sessions.sqlite3'
        self.repo = SQLiteSessionRepository(self.path)
        thread = await self.repo.create_thread()
        self.task = await self.repo.create_task(thread,
            TaskContract('Inspect sources', TaskAuthorization.local_workspace(self.temporary.name)))
        self.task = await self.repo.transition_task(self.task.id, TaskStatus.RUNNING)

    async def asyncTearDown(self):
        self.repo.close()
        self.temporary.cleanup()

    async def snapshot(self):
        return await self.repo._database.read(lambda connection: {
            table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')]
            for table in ('task_executions', 'tasks', 'events', 'checkpoints', 'threads')})

    async def test_exact_registration_is_idempotent_and_preserves_started_at(self):
        await self.repo.register_task_execution(self.task.id, 'instance', 123, 45.0)
        before = await self.snapshot()
        with patch('code_agent.sessions._task_execution.utc_now',
                   return_value=datetime(2099, 1, 1, tzinfo=timezone.utc)):
            await self.repo.register_task_execution(self.task.id, 'instance', 123, 45)
        self.assertEqual(await self.snapshot(), before)

    async def test_different_owner_or_process_cannot_change_any_durable_fact(self):
        await self.repo.register_task_execution(self.task.id, 'instance', 123, 45.0)
        before = await self.snapshot()
        for identity in [('other', 123, 45.0), ('instance', 124, 45.0),
                         ('instance', 123, 46.0)]:
            with self.subTest(identity=identity):
                with self.assertRaisesRegex(ValueError, 'owner|execution'):
                    await self.repo.register_task_execution(self.task.id, *identity)
                self.assertEqual(await self.snapshot(), before)

    async def test_database_reopen_does_not_allow_owner_replacement(self):
        await self.repo.register_task_execution(self.task.id, 'old', 123, 45.0)
        self.repo.close()
        reopened = SQLiteSessionRepository(self.path)
        before = await self.snapshot()
        try:
            with self.assertRaises(ValueError):
                await reopened.register_task_execution(self.task.id, 'new', 124, 46.0)
            self.assertEqual(await self.snapshot(), before)
        finally:
            reopened.close()

    async def test_two_connections_racing_registration_have_one_owner(self):
        other = SQLiteSessionRepository(self.path)
        ready = asyncio.Event()
        identities = [('first', 123, 45.0), ('second', 124, 46.0)]
        async def register(repo, identity):
            await ready.wait()
            try:
                await repo.register_task_execution(self.task.id, *identity)
            except ValueError:
                return False
            return True
        contenders = [asyncio.create_task(register(repo, identity))
                      for repo, identity in zip((self.repo, other), identities)]
        ready.set()
        try:
            accepted = await asyncio.gather(*contenders)
            self.assertEqual(sum(accepted), 1)
            rows = (await self.snapshot())['task_executions']
            self.assertEqual(len(rows), 1)
            owner = rows[0]
            self.assertEqual((owner[1], owner[2], owner[3]), identities[accepted.index(True)])
            self.assertEqual(await self.repo.load_task(self.task.id), self.task)
        finally:
            other.close()

    async def test_explicit_release_or_reconcile_required_before_new_owner(self):
        await self.repo.register_task_execution(self.task.id, 'old', 123, 45.0)
        self.assertTrue(await self.repo.release_task_execution(self.task.id, 'old'))
        await self.repo.register_task_execution(self.task.id, 'new', 124, 46.0)
        self.assertFalse(await self.repo.release_task_execution(self.task.id, 'old'))
        before = await self.snapshot()
        self.assertEqual(before['task_executions'][0][1], 'new')
        self.assertEqual(await self.repo.reconcile_stale_tasks(lambda *_: False), (self.task.id,))
        await self.repo.transition_task(self.task.id, TaskStatus.RUNNING)
        await self.repo.register_task_execution(self.task.id, 'recovered', 125, 47.0)
        self.assertEqual((await self.snapshot())['task_executions'][0][1], 'recovered')

    async def test_begin_rejects_old_paused_owner_without_changing_facts(self):
        await self.repo.register_task_execution(self.task.id, 'old', 123, 45.0)
        paused = await self.repo.transition_task(self.task.id, TaskStatus.PAUSED)
        before = await self.snapshot()
        with self.assertRaisesRegex(ValueError, 'owner|execution'):
            await self.repo.begin_task_execution(self.task.id, 'new', 124, 46.0)
        self.assertEqual(await self.snapshot(), before)
        self.assertEqual(await self.repo.load_task(self.task.id), paused)
        # Repeating the old claim does not resume it or renew either timestamp.
        self.assertEqual(await self.repo.begin_task_execution(self.task.id, 'old', 123, 45), paused)
        self.assertEqual(await self.snapshot(), before)

    async def test_two_connections_racing_begin_paused_task_have_one_owner(self):
        await self.repo.transition_task(self.task.id, TaskStatus.PAUSED)
        other = SQLiteSessionRepository(self.path)
        ready = asyncio.Event()
        identities = [('first', 123, 45.0), ('second', 124, 46.0)]
        async def begin(repo, identity):
            await ready.wait()
            try:
                return await repo.begin_task_execution(self.task.id, *identity)
            except ValueError:
                return None
        contenders = [asyncio.create_task(begin(repo, identity))
                      for repo, identity in zip((self.repo, other), identities)]
        ready.set()
        try:
            outcomes = await asyncio.gather(*contenders)
            accepted = [result is not None for result in outcomes]
            self.assertEqual(sum(accepted), 1)
            winner = outcomes[accepted.index(True)]
            self.assertEqual(winner.status, TaskStatus.RUNNING)
            self.assertEqual(await self.repo.load_task(self.task.id), winner)
            rows = (await self.snapshot())['task_executions']
            self.assertEqual(len(rows), 1)
            self.assertEqual(tuple(rows[0][1:4]), identities[accepted.index(True)])
            before = await self.snapshot()
            with patch('code_agent.sessions._task_execution.utc_now',
                       return_value=datetime(2099, 1, 1, tzinfo=timezone.utc)):
                self.assertEqual(await self.repo.begin_task_execution(
                    self.task.id, *identities[accepted.index(True)]), winner)
            self.assertEqual(await self.snapshot(), before)
        finally:
            other.close()

    async def test_begin_created_task_and_terminal_rejection(self):
        thread = await self.repo.create_thread()
        created = await self.repo.create_task(thread, self.task.contract)
        running = await self.repo.begin_task_execution(created.id, 'created', 123, 45.0)
        self.assertEqual(running.status, TaskStatus.RUNNING)
        self.assertEqual(await self.repo.load_task(created.id), running)
        for status in (TaskStatus.COMPLETED, TaskStatus.FAILED,
                       TaskStatus.SUPERSEDED, TaskStatus.ACCEPTED_PARTIAL):
            with self.subTest(status=status):
                thread = await self.repo.create_thread()
                terminal = await self.repo.create_task(thread, self.task.contract)
                await self.repo.transition_task(terminal.id, TaskStatus.RUNNING)
                if status is TaskStatus.ACCEPTED_PARTIAL:
                    await self.repo.transition_task(terminal.id, TaskStatus.VERIFYING)
                terminal = await self.repo.transition_task(terminal.id, status)
                before = await self.snapshot()
                with self.assertRaises(ValueError):
                    await self.repo.begin_task_execution(terminal.id, 'terminal', 123, 45.0)
                self.assertEqual(await self.snapshot(), before)

    async def test_failed_owner_insert_rolls_back_begin_transition(self):
        await self.repo.transition_task(self.task.id, TaskStatus.PAUSED)
        before = await self.snapshot()
        with patch('code_agent.sessions._task_execution._register',
                   side_effect=RuntimeError('owner insert failed')):
            with self.assertRaisesRegex(RuntimeError, 'owner insert failed'):
                await self.repo.begin_task_execution(self.task.id, 'new', 123, 45.0)
        self.assertEqual(await self.snapshot(), before)

    async def test_register_still_requires_active_task(self):
        await self.repo.transition_task(self.task.id, TaskStatus.PAUSED)
        before = await self.snapshot()
        with self.assertRaisesRegex(ValueError, 'active'):
            await self.repo.register_task_execution(self.task.id, 'new', 123, 45.0)
        self.assertEqual(await self.snapshot(), before)
