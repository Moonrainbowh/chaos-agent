"""Focused offline routing tests; only synthetic prepared bodies and usage."""
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(Path(__file__).parent)]
from adapter import install, MODEL
from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.context_windows.policy import ApiContextLimits, WindowPolicy, RequestBudgetConstraints
from code_agent.core.engine import AgentEngine
from code_agent.core._engine_run import _TurnState
from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import EventKind
from code_agent.core.limits import EngineLimits
from code_agent.core.models import ContextBundle, Message, ModelEvent, ModelEventKind, Usage
from code_agent.core.parent_review import ParentReviewSnapshot
from code_agent.providers.config import ProviderConfig, ApiProtocol, ModelProfile
from code_agent.providers.prepared import PreparedProviderRequest


class Counter:
    def prepared(self, request):
        return len(request.body)


class Store:
    def __init__(self):
        self.records, self.events, self.reserves, self.settled = [], [], [], []
        self.denied = False
        self.direct_usage = 0

    async def context_records(self, thread, kind):
        return list(self.records)

    async def append_event(self, thread, event):
        self.events.append(event)

    async def reserve_context_call(self, thread, identifier, tokens, ceiling, purpose):
        if self.denied:
            raise ValueError('task token budget exhausted')
        self.reserves.append((thread, identifier, tokens, ceiling, purpose))
        return identifier

    async def settle_context_call(self, thread, identifier, usage, estimate, completed):
        self.settled.append((thread, identifier, usage.total_tokens, completed))

    async def mark_task_budget_warnings(self, task):
        return ()

    async def consume_task_usage(self, task, usage):
        self.direct_usage += 1


class Provider:
    def __init__(self, model, **options):
        self.model, self.options, self.sent = model, options, []
        self.closed, self.fail, self.no_usage = False, False, False

    async def prepare_request(self, system, messages, tools):
        body = json.dumps({'model': self.model, 'reasoning_effort': 'medium',
            'max_completion_tokens': 4096, 'system': system,
            'messages': [m.to_dict() for m in messages], 'tools': list(tools)}).encode()
        return PreparedProviderRequest(body, 'https://offline.invalid', (), 4096)

    async def stream_prepared(self, prepared):
        self.sent.append(json.loads(prepared.body))
        if self.fail:
            raise RuntimeError('synthetic failure')
        yield ModelEvent(ModelEventKind.TEXT_DELTA, text='{}')
        if not self.no_usage:
            yield ModelEvent(ModelEventKind.USAGE, usage=Usage(5, 3))
        yield ModelEvent(ModelEventKind.COMPLETED)

    async def aclose(self):
        self.closed = True


class Host:
    def __init__(self, sessions):
        self.sessions = sessions

    async def prepare(self, task, cancellation):
        if self.sessions.records:
            return ParentReviewSnapshot(self.sessions.records[-1])
        return await self.record(task, ParentReviewSnapshot(), {
            'task_id': task.id, 'phase': 'independent', 'objective': 'review',
            'requirements': [], 'sources': [], 'advisory': 'CHILD SECRET',
            'child_objective': 'child', 'initial': '', 'repair_used': False})

    async def record(self, task, snapshot, changes):
        data = {**(snapshot.data or {}), **changes}
        self.sessions.records.append(data)
        return ParentReviewSnapshot(data)


class Engine(AgentEngine):
    async def _build_turn_context(self, state, turn, user_input):
        if state.parent_review is None:
            return ContextBundle('ordinary', (Message('user', 'ordinary'),))
        return await super()._build_turn_context(state, turn, user_input)

    async def _run_turn(self, state, number, user_input):
        await self._prepare_parent_review(state)
        turn, bundles = _TurnState(number, (), set()), []
        async for event in self._start_turn(state, turn, user_input, bundles):
            yield event
        async for event in self._stream_model_events(state, turn, bundles[0]):
            yield event


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store()
        self.base_raw = Provider('glm-5.3-flash')
        self.base = BudgetedWindowClient(self.base_raw, self.store, lambda: 'parent',
            WindowPolicy(work_tokens=300000, safety_tokens=100, task_tokens=1000000),
            ApiContextLimits(300000, 4096), Counter(),
            constraints=RequestBudgetConstraints(host_prompt_tokens=300000))
        self.engine = Engine(self.base, None, None, self.store,
            limits=EngineLimits(12, 40, 10, 1000000), model_name='glm-5.3-flash',
            parent_review=Host(self.store))
        self.engine._journal = self.store
        self.app = SimpleNamespace(controller=SimpleNamespace(_engine=self.engine))
        config = ProviderConfig('https://offline.invalid', MODEL, ApiProtocol.CHAT_COMPLETIONS,
                                'SYNTHETIC_UNUSED_KEY')
        self.profile = ModelProfile('s16-parent-gpt-6-astra', config, 200000, 4096)
        self.raw = None
        def factory(provider, **options):
            self.assertIs(provider, self.profile.provider)
            self.raw = Provider(provider.model, **options)
            return self.raw
        self.handle = await install(self.app, self.profile, client_factory=factory)
        self.state = SimpleNamespace(thread_id='parent', task=SimpleNamespace(id='task'),
            parent_review=None, token=CancellationToken(), total_usage=Usage(),
            pending_runtime_notices=[])

    async def asyncTearDown(self):
        await self.handle.aclose()

    async def run_turn(self):
        return [event async for event in self.engine._run_turn(self.state, 1, '')]

    async def test_two_phases_body_started_usage_and_shared_budget(self):
        await self.run_turn()
        self.assertEqual(self.store.records[-1]['review_model'], self.handle.identity)
        self.assertNotIn('CHILD SECRET', json.dumps(self.raw.sent[0]))
        self.store.records[-1] = {**self.store.records[-1], 'phase': 'comparison', 'initial': '{}'}
        await self.run_turn()
        self.assertIn('CHILD SECRET', json.dumps(self.raw.sent[1]))
        started = [e.payload['model'] for e in self.store.events if e.kind == EventKind.MODEL_STARTED]
        self.assertEqual(started, [MODEL, MODEL])
        self.assertTrue(all(b['model'] == MODEL and not b['tools'] for b in self.raw.sent))
        self.assertEqual(len(self.store.settled), 2)
        self.assertEqual(self.store.direct_usage, 0)
        self.assertTrue(all(r[0] == 'parent' and r[3] == 1000000 for r in self.store.reserves))
        self.assertIs(self.handle.client.sessions, self.base.sessions)
        self.assertIs(self.handle.client.current_thread, self.base.current_thread)
        self.assertLessEqual(self.handle.client.effective_input_cap(), self.base.effective_input_cap())
        self.assertEqual([r['model'] for r in self.handle.requests], [MODEL, MODEL])
        self.assertEqual(self.handle.requests[0]['usage'][0]['input_tokens'], 5)
        self.assertIs(self.engine._model, self.base)
        self.assertIsNone(self.handle.active_phase)

    async def test_ordinary_request_stays_glm_and_repair_stays_new_model(self):
        host = self.engine._parent_review
        self.engine._parent_review = None
        await self.run_turn()
        self.assertEqual(self.base_raw.sent[0]['model'], 'glm-5.3-flash')
        self.assertFalse(self.raw.sent)
        self.engine._parent_review = host
        await self.run_turn()
        self.store.records[-1] = {**self.store.records[-1], 'repair_used': True,
            'repair_phase': 'independent', 'repair_response': '{bad', 'repair': []}
        await self.run_turn()
        self.assertEqual(len(self.raw.sent), 2)
        self.assertTrue(all(r['model'] == MODEL for r in self.handle.requests[1:]))

    async def test_legacy_snapshot_stays_glm(self):
        await self.engine._parent_review.prepare(self.state.task, self.state.token)
        await self.run_turn()
        self.assertFalse(self.raw.sent)
        self.assertEqual(self.base_raw.sent[0]['model'], 'glm-5.3-flash')

    async def test_identity_drift_rejected_before_send(self):
        await self.run_turn()
        self.store.records[-1]['review_model'] = {**self.handle.identity, 'model': 'other'}
        with self.assertRaisesRegex(ValueError, 'identity drift'):
            await self.run_turn()
        self.assertEqual(len(self.raw.sent), 1)
        self.assertIs(self.engine._model, self.base)

    async def test_failure_and_unknown_usage_restore_and_retain_reservation(self):
        self.raw.no_usage = True
        with self.assertRaises(Exception):
            await self.run_turn()
        self.assertEqual(len(self.store.reserves), 1)
        self.assertFalse(self.store.settled)
        self.assertIs(self.engine._model, self.base)
        self.assertEqual(self.handle.requests[0]['usage'], [])

    async def test_budget_denial_sends_nothing(self):
        self.store.denied = True
        with self.assertRaises(Exception):
            await self.run_turn()
        self.assertFalse(self.raw.sent)
        self.assertIs(self.engine._model, self.base)

    async def test_tools_rejected_and_child_not_adapted(self):
        await self.engine._prepare_parent_review(self.state)
        turn = _TurnState(1, ('fake',), {'fake'})
        with self.assertRaisesRegex(ValueError, 'tool-free'):
            _ = [e async for e in self.engine._start_turn(self.state, turn, '', [])]
        child = Engine(self.base, None, None, self.store, model_name='glm-5.3-flash')
        self.assertFalse(hasattr(child, '_review_variant'))
        self.assertIs(child._model, self.base)

    async def test_close_only_extra_provider(self):
        await self.handle.aclose()
        self.assertTrue(self.raw.closed)
        self.assertFalse(self.base_raw.closed)
        self.assertFalse(hasattr(self.engine, '_review_variant'))


if __name__ == '__main__':
    unittest.main()
