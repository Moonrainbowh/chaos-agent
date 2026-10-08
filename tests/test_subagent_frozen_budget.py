"""Production Host composition must borrow the frozen task's actual ceiling."""
import asyncio
from pathlib import Path
import os
import tempfile
from types import SimpleNamespace
import unittest
import uuid

import psutil
from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.limits import EngineLimits
from code_agent.core.models import ActionRequest, Message, ModelEvent, ModelEventKind, Usage
from code_agent.core.task import TaskAuthorization, TaskContract
from code_agent.orchestration.budget import ParentBudget
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from code_agent.sessions.repository import SQLiteSessionRepository
from chaos_agent.agent_modes import build_mode_registry
from chaos_agent.host_composition import compose_subagents, frozen_parent_budget
from chaos_agent.child_runner import EngineChildRunner
from chaos_agent.subagents import SubagentRuntime
from chaos_agent.runtime_extensions import ThreadRuntimeBinding


class FrozenSubagentBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = SQLiteSessionRepository(self.root / 'sessions.sqlite3')
        self.profiles = {'p': ModelProfile('p', ProviderConfig('https://example.test', 'test', ApiProtocol.CHAT_COMPLETIONS, api_key_env='TEST_KEY'), 1000000, 4096)}
        self.modes, _ = build_mode_registry(self.profiles, 'p')
        self.started = 0
        self.durable_usage = False
        owner = self
        class Engine:
            async def run(self, objective, **kwargs):
                owner.started += 1
                yield AgentEvent(EventKind.RUN_STARTED, {'thread_id': kwargs['thread_id']})
                if owner.durable_usage:
                    reservation = await owner.sessions.reserve_context_call(kwargs['thread_id'], 'request', 250000, 1000000, 'offline-unit')
                    await owner.sessions.settle_context_call(kwargs['thread_id'], reservation, Usage(250000, 0), 250000)
                yield AgentEvent(EventKind.MODEL_EVENT, {'event': ModelEvent(ModelEventKind.USAGE, usage=Usage(250000, 0)).to_dict()})
                yield AgentEvent(EventKind.MESSAGE_ADDED, {'message': Message('assistant', 'advisory').to_dict()})
                yield AgentEvent(EventKind.COMPLETED)
        self.runtime, _, _ = compose_subagents(child_engine=lambda *args: (Engine(), None),
            dispatcher=SimpleNamespace(mcp=SimpleNamespace(status=lambda: ())), sessions=self.sessions,
            thread_binding=ThreadRuntimeBinding(), modes=self.modes, profiles=self.profiles,
            plugin_host=None, mode_snapshots={})

    async def asyncTearDown(self):
        await self.runtime.aclose()
        self.sessions.close()
        self.temp.cleanup()

    async def parent(self, tokens=1000000, tools=40, create_budget=True):
        thread = await self.sessions.create_thread()
        task = await self.sessions.create_task(thread, TaskContract('inspect', TaskAuthorization(str(self.root))))
        process = psutil.Process(os.getpid())
        task = await self.sessions.begin_task_execution(task.id, uuid.uuid4().hex, process.pid, process.create_time())
        if create_budget:
            await self.sessions.get_or_create_task_budget(thread, 'test', EngineLimits(max_total_tokens=tokens, max_tool_calls=tools))
        return task

    async def dispatch(self, task, tokens=300000, call='delegate', tools=4):
        activation = self.runtime.activate(task.id)
        try:
            return await self.runtime.dispatch(ActionRequest(call, 'delegate_agent', {'role': 'review', 'objective': 'inspect', 'token_budget': tokens, 'tool_budget': tools}),
                CancellationToken(), execution_context=ActionExecutionContext(task.thread_id, task.thread_id, call, task.id))
        finally:
            self.runtime.reset(activation)

    async def test_actual_compose_uses_frozen_million_for_300k_child(self):
        task = await self.parent()
        result = await self.dispatch(task)
        self.assertFalse(result.is_error, result.output)
        self.assertEqual(self.started, 1)

    async def test_small_frozen_task_rejects_child_despite_profile_capacity(self):
        task = await self.parent(tokens=200000)
        self.assertTrue((await self.dispatch(task)).is_error)
        self.assertEqual(self.started, 0)

    async def test_incomplete_usage_known_lower_bound_not_reset(self):
        task = await self.parent()
        self.assertFalse((await self.dispatch(task)).is_error)
        await self.runtime.release(task.id)
        result = await self.dispatch(task, tokens=800000, call='resumed')
        self.assertTrue(result.is_error)  # Incomplete usage still charges the known 250k; 800k cannot fit.
        self.assertEqual(self.started, 1)

    async def test_release_resume_keeps_settled_usage(self):
        self.durable_usage = True
        task = await self.parent()
        result = await self.dispatch(task)
        self.assertFalse(result.is_error, result.output)
        self.assertTrue(result.output['usage']['complete'])
        await self.runtime.release(task.id)
        self.assertTrue((await self.dispatch(task, tokens=800000, call='too-large')).is_error)
        self.assertFalse((await self.dispatch(task, tokens=740000, call='within-remaining')).is_error)
        self.assertEqual(self.started, 2)

    async def test_missing_context_cannot_poison_valid_task_supervisor(self):
        task = await self.parent()
        activation = self.runtime.activate(task.id)
        try:
            for context in (None, ActionExecutionContext(task.thread_id, task.thread_id, 'bad')):
                with self.assertRaises(ValueError):
                    await self.runtime.dispatch(ActionRequest('bad', 'delegate_agent', {'role': 'review', 'objective': 'inspect'}), CancellationToken(), execution_context=context)
        finally:
            self.runtime.reset(activation)
        self.assertFalse((await self.dispatch(task)).is_error)

    async def test_cached_supervisor_rejects_foreign_owner(self):
        task = await self.parent()
        self.assertFalse((await self.dispatch(task)).is_error)
        activation = self.runtime.activate(task.id)
        try:
            with self.assertRaises(ValueError):
                await self.runtime.dispatch(ActionRequest('foreign', 'delegate_agent', {'role': 'review', 'objective': 'inspect'}), CancellationToken(), execution_context=ActionExecutionContext('foreign', 'foreign', 'foreign', task.id))
        finally:
            self.runtime.reset(activation)
        self.assertEqual(self.started, 1)

    async def test_frozen_tool_ceiling_rejects_request(self):
        task = await self.parent(tools=3)
        self.assertTrue((await self.dispatch(task)).is_error)
        self.assertEqual(self.started, 0)

    async def test_explicit_budget_can_only_tighten_frozen_task(self):
        await self.runtime.aclose()
        self.runtime = SubagentRuntime(EngineChildRunner(lambda *args: self.fail('must not start'), sessions=self.sessions), self.modes, self.profiles, ParentBudget(), budget_resolver=lambda context: frozen_parent_budget(self.sessions, context))
        task = await self.parent()
        self.assertTrue((await self.dispatch(task)).is_error)

    async def test_first_foreign_owner_and_mismatched_task_fail_closed(self):
        task = await self.parent()
        activation = self.runtime.activate(task.id)
        try:
            for context in (ActionExecutionContext('foreign', 'foreign', 'bad', task.id), ActionExecutionContext(task.thread_id, task.thread_id, 'bad', 'different-task')):
                with self.assertRaises(ValueError):
                    await self.runtime.dispatch(ActionRequest('bad', 'delegate_agent', {'role': 'review', 'objective': 'inspect'}), CancellationToken(), execution_context=context)
        finally:
            self.runtime.reset(activation)
        self.assertFalse((await self.dispatch(task)).is_error)

    async def test_missing_frozen_budget_fails_closed(self):
        task = await self.parent(create_budget=False)
        with self.assertRaises((ValueError, RuntimeError)):
            await self.dispatch(task)
        self.assertEqual(self.started, 0)

    async def test_concurrent_first_calls_share_one_ledger(self):
        task = await self.parent(tokens=400000)
        results = await asyncio.gather(self.dispatch(task, call='a'), self.dispatch(task, call='b'))
        self.assertEqual(sum(not result.is_error for result in results), 1)
        self.assertEqual(self.started, 1)
