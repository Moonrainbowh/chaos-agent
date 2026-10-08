from __future__ import annotations

import asyncio
import unittest
import tempfile
from pathlib import Path

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.interfaces.approval import ApprovalBroker, ApprovalRequest


class FakeRepository:
    def __init__(self):
        self.rows = {}
        self.calls = 0
        self.stale = False

    async def approval_binding(self, task_id, workspace_root):
        return dict(task_id=task_id, thread_id='thread', workspace_root=workspace_root,
                    state_version='version', owner_instance_id='instance')

    async def create_approval_request(self, **values):
        row = {**values.pop('binding'), **values, 'status': 'pending', 'response': None}
        self.rows[row['request_id']] = row
        return dict(row)

    async def list_approval_requests(self, task_id, limit=100):
        return tuple(dict(row) for row in self.rows.values() if row['task_id'] == task_id)[:limit]

    async def consume_approval_request(self, request_id, **response):
        row = self.rows[request_id]
        consumed_now = row['status'] == 'pending'
        if self.stale or row['status'] == 'stale':
            raise ValueError('stale')
        if row['status'] == 'consumed' and row['response'] != response['approved']:
            raise ValueError('conflict')
        if row['status'] == 'pending':
            self.calls += 1
            row.update(status='consumed', response=response['approved'])
        return {**row, 'consumed_now': consumed_now}

    async def invalidate_approval_requests(self, *, request_id):
        row = self.rows[request_id]
        if row['status'] == 'pending':
            row['status'] = 'stale'
            return 1
        return 0


class ApprovalPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def waiting(self, *, ttl=300):
        repo = FakeRepository()
        broker = ApprovalBroker(repo, ttl_seconds=ttl)
        token = CancellationToken()
        request = ApprovalRequest('call', 'write_file', {'path': 'x', 'content': 'SECRET'})
        context = ActionExecutionContext('thread', 'thread', 'call', 'task')
        waiting = asyncio.create_task(broker.request(request, token,
            execution_context=context, workspace_root='Host-root'))
        self.assertEqual(await broker.next_request(), request)
        card = (await broker.pending('task'))[0]
        answer = {key: card[key] for key in ('task_id', 'action_digest', 'state_version', 'owner_instance_id')}
        return repo, broker, token, waiting, card, answer

    async def test_remote_response_commits_before_waking_and_replay_never_executes(self):
        repo, broker, _, waiting, card, answer = await self.waiting()
        self.assertNotEqual(card['request_id'], 'call')
        self.assertNotIn('SECRET', str(card['preview']))
        response = await broker.respond(card['request_id'], **answer, approved=True, authenticate=lambda: True)
        self.assertEqual(response['status'], 'consumed')
        await broker.respond(card['request_id'], **answer, approved=True, authenticate=lambda: True)
        self.assertTrue(await waiting)
        self.assertEqual(repo.calls, 1)
        with self.assertRaises(ValueError):
            await broker.respond(card['request_id'], **answer, approved=True, authenticate=lambda: True)

    async def test_wrong_bindings_and_revoked_device_do_not_wake(self):
        repo, broker, token, waiting, card, answer = await self.waiting()
        for key in answer:
            with self.assertRaises(ValueError):
                await broker.respond(card['request_id'], **{**answer, key: 'wrong'}, approved=True, authenticate=lambda: True)
        with self.assertRaises(PermissionError):
            await broker.respond(card['request_id'], **answer, approved=True, authenticate=lambda: False)
        self.assertFalse(waiting.done())
        self.assertEqual(repo.calls, 0)
        token.cancel('test cancellation')
        with self.assertRaises(CancellationError):
            await waiting
        self.assertEqual(repo.rows[card['request_id']]['status'], 'stale')

    async def test_local_keyboard_requires_durable_cas_before_approval_returns(self):
        repo, broker, _, waiting, card, _ = await self.waiting()
        repo.stale = True
        self.assertTrue(broker.resolve('call', True))
        with self.assertRaises(ValueError):
            await waiting
        self.assertEqual(repo.calls, 0)

    async def test_deadline_denies_without_consumption(self):
        repo, _, _, waiting, card, _ = await self.waiting(ttl=0.02)
        self.assertFalse(await waiting)
        self.assertEqual(repo.calls, 0)
        self.assertEqual(repo.rows[card['request_id']]['status'], 'stale')

    async def test_unbound_approval_is_local_only(self):
        repo = FakeRepository()
        broker = ApprovalBroker(repo)
        waiting = asyncio.create_task(broker.request(ApprovalRequest('call', 'plugin', {}), CancellationToken()))
        await broker.next_request()
        self.assertEqual(await broker.pending('task'), ())
        self.assertTrue(broker.resolve('call', False))
        self.assertFalse(await waiting)
        self.assertEqual(repo.rows, {})

    async def test_restart_broker_does_not_restore_waiter(self):
        repo, broker, token, waiting, card, answer = await self.waiting()
        restarted = ApprovalBroker(repo)
        self.assertEqual(len(await restarted.pending('task')), 1)
        with self.assertRaises(ValueError):
            await restarted.respond(card['request_id'], **answer, approved=True, authenticate=lambda: True)
        self.assertEqual(repo.calls, 0)
        token.cancel()
        with self.assertRaises(CancellationError):
            await waiting

    async def test_invalid_context_never_falls_back_to_local_approval(self):
        broker = ApprovalBroker(FakeRepository())
        with self.assertRaises(TypeError):
            await broker.request(ApprovalRequest('call', 'write_file', {}), CancellationToken(),
                execution_context={'task_id': 'task'}, workspace_root='root')
        self.assertEqual(broker._pending, {})

    async def test_external_consumption_cannot_wake_this_waiter(self):
        repo, broker, token, waiting, card, answer = await self.waiting()
        await repo.consume_approval_request(card['request_id'], **answer, approved=True)
        with self.assertRaises(ValueError):
            await broker.respond(card['request_id'], **answer, approved=True, authenticate=lambda: True)
        self.assertFalse(waiting.done())
        token.cancel()
        with self.assertRaises(CancellationError):
            await waiting


class DurableApprovalBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from code_agent.sessions.repository import SQLiteSessionRepository
        from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / 'sessions.sqlite3'
        assert self.path.is_absolute() and self.path.parent == self.root
        self.repo = SQLiteSessionRepository(self.path)
        self.thread = await self.repo.create_thread()
        self.task = await self.repo.create_task(self.thread, TaskContract('test',
            TaskAuthorization.local_workspace(str(self.root))))
        await self.repo.transition_task(self.task.id, TaskStatus.RUNNING)
        await self.repo.register_task_execution(self.task.id, 'owner', 12, 34.0)
        self.broker = ApprovalBroker(self.repo)

    async def asyncTearDown(self):
        self.repo.close()
        self.temp.cleanup()

    async def waiting(self):
        token = CancellationToken()
        waiter = asyncio.create_task(self.broker.request(
            ApprovalRequest('call', 'write_file', {'path': 'x', 'content': 'secret'}), token,
            execution_context=ActionExecutionContext(self.thread, self.thread, 'call', self.task.id),
            workspace_root=str(self.root)))
        await self.broker.next_request()
        card = (await self.broker.pending(self.task.id))[0]
        return token, waiter, card

    async def test_real_repository_keyboard_denial_is_durable(self):
        _, waiter, card = await self.waiting()
        self.broker.resolve('call', False)
        self.assertFalse(await waiter)
        persisted = (await self.repo.list_approval_requests(self.task.id))[0]
        self.assertEqual(persisted['request_id'], card['request_id'])
        self.assertEqual(persisted['status'], 'denied')

    async def test_real_repository_remote_approval_and_cancelled_card(self):
        _, waiter, card = await self.waiting()
        fields = {key: card[key] for key in ('task_id', 'action_digest', 'state_version', 'owner_instance_id')}
        result = await self.broker.respond(card['request_id'], **fields,
            approved=True, authenticate=lambda: True)
        self.assertEqual(result['status'], 'approved')
        self.assertTrue(await waiter)
        token, waiter, card = await self.waiting()
        token.cancel()
        with self.assertRaises(CancellationError):
            await waiter
        records = await self.repo.list_approval_requests(self.task.id)
        self.assertEqual(next(row for row in records if row['request_id'] == card['request_id'])['status'], 'stale')

    async def test_real_repository_drift_blocks_local_approval(self):
        from code_agent.core.task_state import TaskState
        from dataclasses import replace
        _, waiter, _ = await self.waiting()
        await self.repo.save_task_state(self.thread, replace(TaskState.empty(), code_generation=1))
        self.broker.resolve('call', True)
        with self.assertRaises(ValueError):
            await waiter


if __name__ == '__main__':
    unittest.main()
