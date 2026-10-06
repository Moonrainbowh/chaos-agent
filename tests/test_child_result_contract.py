import unittest
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.cancellation import CancellationToken
from code_agent.orchestration.models import AgentDefinition, AgentRole, ChildRunRequest, RunStatus
from tests.test_subagent_integration import _runtime
from chaos_agent.child_runner import EngineChildRunner


class ChildResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_cumulative_usage_counts_each_request_once_even_if_cancelled(self):
        from chaos_agent.child_result import collect_child_result
        from code_agent.core.models import ModelEvent, ModelEventKind, Usage
        registry, profiles = _runtime()
        agent = AgentDefinition('reviewer', AgentRole.REVIEW,
            registry.freeze('medium', profiles), 'Review.', ('read_file',))
        request = ChildRunRequest('parent', 'review', agent, 1, 100, 4, 30)
        async def stream():
            yield AgentEvent(EventKind.RUN_STARTED, {'thread_id': 'child'})
            for snapshots in ((Usage(14, 1), Usage(14, 7)), (Usage(8, 1), Usage(8, 2))):
                yield AgentEvent(EventKind.MODEL_STARTED)
                for usage in snapshots:
                    event = ModelEvent(ModelEventKind.USAGE, usage=usage)
                    yield AgentEvent(EventKind.MODEL_EVENT, {'event': event.to_dict()})
            yield AgentEvent(EventKind.CANCELLED)
        class Sessions:
            async def context_records(self, thread, kind):
                return ({'status': 'settled', 'usage': {'input_tokens': 14, 'output_tokens': 7}},
                        {'status': 'partial', 'usage': {'input_tokens': 8, 'output_tokens': 2}})
        for sessions in (None, Sessions()):
            result = await collect_child_result(stream(), request, sessions=sessions)
            self.assertEqual(result.usage.total_tokens, 31)
            self.assertFalse(result.usage_complete)
            self.assertEqual(result.status, RunStatus.CANCELLED)

    async def test_partial_provider_usage_is_explicitly_a_lower_bound(self):
        from chaos_agent.child_result import collect_child_result
        registry, profiles = _runtime()
        agent = AgentDefinition('reviewer', AgentRole.REVIEW,
            registry.freeze('medium', profiles), 'Review.', ('read_file',))
        async def stream():
            yield AgentEvent(EventKind.RUN_STARTED, {'thread_id': 'child'})
            yield AgentEvent(EventKind.CANCELLED)
        class Sessions:
            async def context_records(self, thread, kind):
                return ({'status': 'partial', 'usage': {'input_tokens': 17, 'output_tokens': 3}},)
        result = await collect_child_result(stream(),
            ChildRunRequest('parent', 'review', agent, 1, 100, 4, 30), sessions=Sessions())
        self.assertEqual(result.usage.total_tokens, 20)
        self.assertFalse(result.usage_complete)
        self.assertEqual(result.status, RunStatus.CANCELLED)

    async def test_cancelled_run_survives_later_stream_failure(self):
        registry, profiles = _runtime()
        agent = AgentDefinition('reviewer', AgentRole.REVIEW,
                                registry.freeze('medium', profiles), 'Review.', ('read_file',))
        class Engine:
            async def run(self, *args, **kwargs):
                yield AgentEvent(EventKind.CANCELLED, {'reason': 'explicit cancellation'})
                raise RuntimeError('secret stream cleanup details')
        request = ChildRunRequest('parent', 'review', agent, 1, 100, 4, 30)
        for runner_type in (EngineChildRunner,):
            with self.subTest(runner=runner_type):
                result = await runner_type(lambda *args: (Engine(), None)).run(request, CancellationToken())
                self.assertEqual(result.status, RunStatus.CANCELLED)
                self.assertEqual(result.result.execution_status, 'cancelled')
                self.assertEqual(result.result.exit_code(), 130)
                self.assertEqual(result.result.stop_reason, 'explicit cancellation')
                self.assertNotIn('secret', result.error)

    async def test_production_runner_uses_terminal_facts(self):
        registry, profiles = _runtime()
        agent = AgentDefinition('reviewer', AgentRole.REVIEW,
                                registry.freeze('medium', profiles), 'Review.', ('read_file',))
        request = ChildRunRequest('parent', 'review', agent, 1, 100, 4, 30)
        for runner_type in (EngineChildRunner,):
            for kinds, expected in [((), RunStatus.INTERRUPTED),
                ((EventKind.CANCELLED,), RunStatus.CANCELLED),
                ((EventKind.ERROR,), RunStatus.FAILED),
                ((EventKind.TASK_DECISION_REQUIRED,), RunStatus.WAITING_DECISION),
                ((EventKind.COMPLETED,), RunStatus.COMPLETED),
                ((EventKind.ERROR, EventKind.COMPLETED), RunStatus.COMPLETED)]:
                with self.subTest(runner=runner_type, events=kinds):
                    class Engine:
                        async def run(self, *args, **kwargs):
                            for kind in kinds:
                                yield AgentEvent(kind)
                    result = await runner_type(lambda *args: (Engine(), None)).run(request, CancellationToken())
                    self.assertEqual(result.status, expected)
                    self.assertNotEqual(result.result.verification_status, 'verified')

    async def test_exception_is_failed_with_safe_reason(self):
        registry, profiles = _runtime()
        agent = AgentDefinition('reviewer', AgentRole.REVIEW,
                                registry.freeze('medium', profiles), 'Review.', ('read_file',))
        class Engine:
            async def run(self, *args, **kwargs):
                raise RuntimeError('secret provider content')
                yield None
        result = await EngineChildRunner(lambda *args: (Engine(), None)).run(
            ChildRunRequest('parent', 'review', agent, 1, 100, 4, 30), CancellationToken())
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertNotIn('secret', result.error)
