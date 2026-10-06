import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.context_windows.policy import WindowPolicy
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.policy.models import ApprovalMode
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.context.repo_index import RepoIndexService
from chaos_agent.action_dispatcher import RootActionDispatcher
from chaos_agent.application_context import engine_for
from chaos_agent.context_assembly import ContextAssembly, ContextScopedDispatcher, current_context_actions, wrap_context
from chaos_agent.restricted_dispatcher import RestrictedDispatcher
from tests import test_thread_intelligence_runtime as fixture
_mode = fixture._mode


class ExplicitContextAssemblyTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ProductionThreadContextTests.setUp
    tearDown = fixture.ProductionThreadContextTests.tearDown

    async def test_source_task_and_child_have_explicit_distinct_snapshot_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            task_root = Path(temporary).resolve()
            guard = WorkspacePathGuard(task_root)
            files = WorkspaceFiles(guard, IgnoreRules.from_workspace(task_root))
            index = RepoIndexService(files)
            services = SimpleNamespace(guard=guard, files=files, repo_index=index)
            class Runtime:
                def services_for_root(self, root):
                    if root != task_root:
                        raise AssertionError(root)
                    return services
                def root_for_thread(self, thread):
                    return task_root
                async def hydrate_bindings(self):
                    pass
            self.factory._workspace_runtime = Runtime()
            main = self.factory(_mode(self.profile), self.model, self.profile)
            child = self.factory.for_child(_mode(self.profile), self.model,
                self.profile, task_root, self.sessions)
            self.assertIs(main.semantic_snapshot(self.root), self.repo_index.snapshot_for_turn())
            self.assertIs(main.semantic_snapshot(task_root), index.snapshot_for_turn())
            self.assertIs(child.semantic_snapshot(task_root), index.snapshot_for_turn())
            with self.assertRaises(ValueError):
                child.semantic_snapshot(self.root)
            self.assertIs(child.model_client.model, self.model)

    async def test_managed_actions_route_to_actual_task_root_builder(self):
        # Persistent tools carry the active builder's measured remaining tokens.
        class Runtime:
            def root_for_thread(runtime, thread):
                return self.root
            async def hydrate_bindings(runtime):
                pass
        self.factory._workspace_runtime = Runtime()
        profile = replace(self.profile, context_policy=WindowPolicy(strategy='persistent', work_tokens=32000))
        context = self.factory(_mode(profile), self.model, profile)
        thread = await self.sessions.create_thread()
        await self.sessions.append_message(thread, fixture.Message('user', 'inspect root'))
        token = self.binding.bind(thread)
        try:
            bundle = await context.build(fixture.ContextRequest(thread, 1, (), '', (),
                fixture.TaskState(), CancellationToken()))
            result = await context.context_actions.dispatch(
                ActionRequest('remaining', 'get_context_remaining', {}), CancellationToken())
        finally:
            self.binding.reset(token)
        self.assertFalse(result.is_error, result.output)
        self.assertIsNotNone(bundle.measurements)
        self.assertNotEqual(result.output, {})
    def test_all_strategies_deliver_single_guard_and_root_bound_snapshot(self):
        for strategy in (None, 'boundary', 'persistent'):
            with self.subTest(strategy=strategy):
                profile = self.profile if strategy is None else replace(self.profile,
                    context_policy=WindowPolicy(strategy=strategy, work_tokens=32000))
                context = self.factory(_mode(profile), self.model, profile)
                self.assertIsInstance(context, ContextAssembly)
                self.assertIsInstance(context.model_client, BudgetedWindowClient)
                self.assertIs(context.model_client.model, self.model)
                self.assertIs(context.semantic_snapshot(self.root), self.repo_index.snapshot_for_turn())
                with self.assertRaises(ValueError):
                    context.semantic_snapshot(self.root / 'other')
                wrapper = SimpleNamespace
                wrapped = wrap_context(context, lambda builder: wrapper(build=builder.build))
                self.assertIs(wrapped.model_client, context.model_client)
                self.assertIs(wrapped.context_actions, context.context_actions)
                self.assertIs(wrapped.semantic_snapshot, context.semantic_snapshot)

    async def test_managed_tools_reach_central_policy_without_mutating_shared_root(self):
        thread = await self.sessions.create_thread()
        profile = replace(self.profile, context_policy=WindowPolicy(work_tokens=32000))
        context = self.factory(_mode(profile), self.model, profile)
        root = RootActionDispatcher(self.files, WorkspaceEditor(self.guard),
            ActionPolicy(PolicyConfig(approval_mode=ApprovalMode.FULL_LOCAL, workspace_root=self.root)), ApprovalBroker())
        mode = _mode(profile)
        restricted = RestrictedDispatcher(root, ('context_note', 'context_history', 'new_context'))
        engine = engine_for(self.model, profile, context, restricted, self.sessions, self.root, mode)
        self.assertIn('context_note', {t.name for t in engine._actions.tools()})
        token = self.binding.bind(thread)
        try:
            result = await engine._actions.dispatch(ActionRequest('n1', 'context_note',
                {'operation': 'write', 'text': 'UNVERIFIED_NOTE'}), CancellationToken())
        finally:
            self.binding.reset(token)
        self.assertFalse(result.is_error, result.output)
        self.assertEqual(len(await self.sessions.context_records(thread, 'note')), 1)
        self.assertIsNone(current_context_actions())
        self.assertNotIn('context_note', {t.name for t in root.tools()})


class ScopedContextDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_runners_and_cancel_restore_scope(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        observations = []
        class CentralDispatcher:
            def tools(self):
                return (current_context_actions(),)
            async def dispatch(self, request):
                observations.append((request, current_context_actions()))
                entered.set()
                await release.wait()
                observations.append((request, current_context_actions()))
                return request
        central = CentralDispatcher()
        first, second = ContextScopedDispatcher(central, 'first'), ContextScopedDispatcher(central, 'second')
        self.assertEqual(first.tools(), ('first',))
        self.assertEqual(second.tools(), ('second',))
        one = asyncio.create_task(first.dispatch('one'))
        await entered.wait()
        two = asyncio.create_task(second.dispatch('two'))
        await asyncio.sleep(0)
        one.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await one
        release.set()
        await two
        self.assertEqual(observations, [('one', 'first'), ('two', 'second'), ('two', 'second')])
        self.assertIsNone(current_context_actions())
