"""Shared admission is durable and rejects without partially charging siblings."""
import asyncio
import tempfile
import unittest
from pathlib import Path

from code_agent.core.limits import EngineLimits, BudgetReserveStatus
from code_agent.core.models import Usage
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.sessions.repository import SQLiteSessionRepository


class SharedBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'sessions.sqlite3'
        self.repo = SQLiteSessionRepository(self.path)
        self.owner = await self.repo.create_thread()
        self.task = await self.repo.create_task(self.owner, TaskContract('inspect',
            TaskAuthorization.local_workspace(self.temp.name)))
        await self.repo.transition_task(self.task.id, TaskStatus.RUNNING)
        await self.repo.register_task_execution(self.task.id, 'owner', 123, 45)
        await self.repo.get_or_create_task_budget(self.owner, 'model',
            EngineLimits(max_agent_rounds=4, max_tool_calls=3, max_total_tokens=1000))

    async def asyncTearDown(self):
        self.repo.close()
        self.temp.cleanup()

    async def child(self, request, *, tokens=800, tools=2, children=8):
        child = await self.repo.create_thread(parent_thread_id=self.owner)
        await self.repo.bind_child_budget(child, self.owner, self.task.id, request,
            max_total_tokens=tokens, max_tool_calls=tools, max_children=children)
        await self.repo.get_or_create_task_budget(child, 'model',
            EngineLimits(max_agent_rounds=10, max_tool_calls=10, max_total_tokens=1000))
        return child

    async def budget(self, thread):
        return await self.repo.get_or_create_task_budget(thread, 'model', EngineLimits())

    async def test_concurrent_parent_and_siblings_share_remaining_token_capacity(self):
        first, second = await self.child('a'), await self.child('b')
        results = await asyncio.gather(*(self.repo.reserve_context_call(t, t, 600, 1000, 'main')
            for t in (first, second, self.owner)), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, str) for r in results), 1)
        self.assertEqual(sum(isinstance(r, ValueError) for r in results), 2)
        self.assertEqual(len(await self.repo.context_records(self.owner, 'usage')), 1)

    async def test_child_ceiling_does_not_lower_shared_owner_ceiling(self):
        child = await self.child('a', tokens=300)
        await self.repo.reserve_context_call(child, 'child', 200, 300, 'main')
        await self.repo.reserve_context_call(self.owner, 'parent', 700, 1000, 'main')
        with self.assertRaises(ValueError):
            await self.repo.reserve_context_call(child, 'too-much', 101, 300, 'main')

    async def test_tool_failure_rolls_back_local_and_owner_atomically(self):
        child = await self.child('a')
        await self.repo.reserve_task_budget(self.owner, tool_calls=2)
        result = await self.repo.reserve_task_budget(child, tool_calls=2)
        self.assertEqual(result.status, BudgetReserveStatus.HARD_EXHAUSTED)
        self.assertEqual((await self.budget(child)).tool_calls, 0)
        self.assertEqual((await self.budget(self.owner)).tool_calls, 2)
        result = await self.repo.reserve_task_budget(child, tool_calls=1)
        self.assertEqual(result.status, BudgetReserveStatus.RESERVED)
        self.assertEqual((await self.budget(child)).tool_calls, 1)
        self.assertEqual((await self.budget(self.owner)).tool_calls, 3)

    async def test_model_turns_shared_and_zero_child_tools_forbidden(self):
        child = await self.child('a', tools=0)
        self.assertEqual((await self.repo.reserve_task_budget(child, tool_calls=1)).status,
                         BudgetReserveStatus.HARD_EXHAUSTED)
        await self.repo.reserve_task_budget(self.owner, model_turns=3)
        self.assertEqual((await self.repo.reserve_task_budget(child, model_turns=1)).status,
                         BudgetReserveStatus.RESERVED)
        self.assertEqual((await self.repo.reserve_task_budget(child, model_turns=1)).status,
                         BudgetReserveStatus.HARD_EXHAUSTED)
        self.assertEqual((await self.budget(child)).model_turns, 1)

    async def test_pending_unknown_at_token_cap_blocks_new_tools(self):
        await self.repo.reserve_context_call(self.owner, 'unknown', 1000, 1000, 'main')
        result = await self.repo.reserve_task_budget(self.owner, tool_calls=1)
        self.assertEqual(result.status, BudgetReserveStatus.HARD_EXHAUSTED)
        self.assertEqual((await self.budget(self.owner)).tool_calls, 0)

    async def test_concurrent_sibling_tools_consume_shared_cap_only_once(self):
        first, second = await self.child('a'), await self.child('b')
        results = await asyncio.gather(*(self.repo.reserve_task_budget(t, tool_calls=2)
            for t in (first, second)))
        self.assertEqual(sum(r.status is BudgetReserveStatus.RESERVED for r in results), 1)
        self.assertEqual((await self.budget(self.owner)).tool_calls, 2)
        budgets = await asyncio.gather(self.budget(first), self.budget(second))
        self.assertEqual(sum(b.tool_calls for b in budgets), 2)

    async def test_partial_unknown_reopen_and_duplicate_settlement(self):
        child = await self.child('a')
        request = await self.repo.reserve_context_call(child, 'req', 800, 1000, 'main')
        await self.repo.settle_context_call(child, request, Usage(100, 20), 110, completed=False)
        await self.repo.settle_context_call(child, request, Usage(100, 20), 110, completed=False)
        self.assertEqual((await self.budget(self.owner)).input_tokens, 100)
        self.repo.close()
        self.repo = SQLiteSessionRepository(self.path)
        with self.assertRaises(ValueError):
            await self.repo.reserve_context_call(self.owner, 'next', 201, 1000, 'main')
        await self.repo.settle_context_call(child, request, Usage(), 0)
        self.assertEqual((await self.repo.context_records(child, 'usage'))[0]['status'], 'partial')
        await self.repo.settle_context_call(child, request, Usage(110, 30), 110)
        await self.repo.settle_context_call(child, request, Usage(110, 30), 110)
        self.assertEqual((await self.budget(self.owner)).input_tokens, 110)
        self.assertEqual((await self.budget(child)).output_tokens, 30)
        with self.assertRaises(ValueError):
            await self.repo.settle_context_call(child, request, Usage(111, 30), 110)
        with self.assertRaises(ValueError):
            await self.repo.settle_context_call(child, request, Usage(), 110)
        await self.repo.reserve_context_call(self.owner, 'available', 850, 1000, 'main')

    async def test_overrun_persists_actual_and_blocks_subsequent_admission(self):
        child = await self.child('a')
        request = await self.repo.reserve_context_call(child, 'req', 500, 1000, 'main')
        await self.repo.settle_context_call(child, request, Usage(1000, 100), 200)
        self.assertEqual((await self.repo.load_task_budget(self.task.id)).input_tokens, 1000)
        self.assertEqual((await self.budget(child)).output_tokens, 100)
        with self.assertRaises(ValueError):
            await self.repo.reserve_context_call(self.owner, 'next', 1, 1000, 'main')
        self.assertEqual((await self.repo.reserve_task_budget(self.owner, tool_calls=1)).status,
                         BudgetReserveStatus.HARD_EXHAUSTED)

    async def test_identity_duplicate_children_limit_and_restart_do_not_gift_quota(self):
        child = await self.child('a', children=1)
        await self.repo.bind_child_budget(child, self.owner, self.task.id, 'a',
            max_total_tokens=800, max_tool_calls=2, max_children=1)
        other = await self.repo.create_thread(parent_thread_id=self.owner)
        for request in ('a', 'b'):
            with self.assertRaises(ValueError):
                await self.repo.bind_child_budget(other, self.owner, self.task.id, request,
                    max_total_tokens=800, max_tool_calls=2, max_children=1)
        self.repo.close()
        self.repo = SQLiteSessionRepository(self.path)
        with self.assertRaises(ValueError):
            await self.repo.bind_child_budget(other, self.owner, self.task.id, 'b',
                max_total_tokens=800, max_tool_calls=2, max_children=1)
        with self.assertRaises(ValueError):
            await self.repo.bind_child_budget(other, self.owner, self.task.id, 'b',
                max_total_tokens=800, max_tool_calls=2, max_children=100)

    async def test_parent_inactive_cross_owner_and_wrong_origin_refused(self):
        child = await self.child('a')
        request = await self.repo.reserve_context_call(child, 'req', 200, 1000, 'main')
        with self.assertRaises(ValueError):
            await self.repo.settle_context_call(self.owner, request, Usage(10, 1), 10)
        foreign = await self.repo.create_thread()
        with self.assertRaises(ValueError):
            await self.repo.bind_child_budget(foreign, self.owner, self.task.id, 'b',
                max_total_tokens=800, max_tool_calls=2)
        await self.repo.release_task_execution(self.task.id, 'owner')
        with self.assertRaises(ValueError):
            await self.repo.reserve_context_call(child, 'later', 1, 1000, 'main')
        # Settlement remains permitted after cancellation/owner release.
        await self.repo.settle_context_call(child, request, Usage(10, 1), 10)

    async def test_old_child_cannot_resume_under_new_parent_owner_instance(self):
        child = await self.child('a')
        await self.repo.release_task_execution(self.task.id, 'owner')
        await self.repo.register_task_execution(self.task.id, 'new-owner', 124, 46)
        with self.assertRaises(ValueError):
            await self.repo.reserve_context_call(child, 'old-child-next', 1, 1000, 'main')
        with self.assertRaises(ValueError):
            await self.repo.reserve_task_budget(child, tool_calls=1)


if __name__ == '__main__':
    unittest.main()
