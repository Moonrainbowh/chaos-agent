"""Exercise Host and semantic-summary constraints at their production entry points."""
import unittest
from types import SimpleNamespace

from code_agent.context_windows.client import BudgetedWindowClient, RequestCapacityError
from code_agent.context_windows.policy import ApiContextLimits, WindowPolicy, RequestBudgetConstraints
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import Message, ModelEvent, ModelEventKind, Usage
from code_agent.thread_intelligence.models import SummaryRequest, anchor_message
from chaos_agent.application_context import RuntimeContextFactory, _profile_prompt_budget
from chaos_agent.runtime_extensions import ModelSemanticSummarizer
from chaos_agent.runtime_extensions import ThreadRuntimeBinding
from chaos_agent.context_assembly import ContextAssembly
from tests.test_thread_intelligence_runtime import _profile, _mode


class HostRequestConstraintsTests(unittest.IsolatedAsyncioTestCase):
    async def test_cold_compact_binds_caller_and_resets_after_failure(self):
        binding = ThreadRuntimeBinding()
        observed = []
        class Builder:
            async def compact_context(self, thread_id, cancellation):
                observed.append(binding.current())
                if thread_id == "failed-thread":
                    raise RuntimeError("migration failed")
                return "published"
        assembly = ContextAssembly(Builder(), object(), compact_binding=binding)
        self.assertEqual(await assembly.compact_context("cold-thread", CancellationToken()), "published")
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            binding.current()
        outer = binding.bind("outer-thread")
        try:
            with self.assertRaisesRegex(RuntimeError, "migration failed"):
                await assembly.compact_context(thread_id="failed-thread", cancellation=CancellationToken())
            self.assertEqual(binding.current(), "outer-thread")
        finally:
            binding.reset(outer)
        self.assertEqual(observed, ["cold-thread", "failed-thread"])

    async def test_preflight_never_admits_and_cannot_authorize_changed_request(self):
        class Sessions:
            async def reserve_context_call(self, *args):
                raise AssertionError("preflight/over-cap must not reserve")
        class Model:
            def stream(self, *args):
                raise AssertionError("preflight/over-cap must not send")
        class Counter:
            estimate = 5
            def request(self, *args):
                return self.estimate
        counter = Counter()
        guard = BudgetedWindowClient(Model(), Sessions(), lambda: "thread",
            WindowPolicy(work_tokens=500, safety_tokens=1, task_tokens=1000),
            ApiContextLimits(500, 10), counter,
            constraints=RequestBudgetConstraints(host_prompt_tokens=100))
        self.assertEqual(guard.effective_input_cap(), 99)
        self.assertEqual(guard.effective_input_cap(output_tokens=20,
            constraints=RequestBudgetConstraints(auxiliary_total_tokens=40)), 19)
        self.assertEqual(guard.effective_input_cap(output_tokens=450), 49)
        self.assertEqual(await guard.preflight_request("system", (), ()), (None, 5, 10))
        with self.assertRaises(RequestCapacityError):
            await guard.preflight_request("system", (), (),
                constraints=RequestBudgetConstraints(auxiliary_input_tokens=4))
        counter.estimate = 500
        with self.assertRaises(RequestCapacityError):
            _ = [event async for event in guard.stream_for("handoff", "changed", (), ())]

    def test_host_constructs_guard_with_actual_prompt_ceiling(self):
        profile = _profile()
        mode = _mode(profile)
        factory = RuntimeContextFactory.__new__(RuntimeContextFactory)
        factory._sessions = object()
        factory._thread_binding = SimpleNamespace(current=lambda: "thread")
        guarded = factory._budgeted_client(mode, object(), profile)
        self.assertEqual(guarded.constraints.host_prompt_tokens,
                         _profile_prompt_budget(profile, mode).max_prompt_tokens)
        permissive = BudgetedWindowClient(object(), object(), lambda: "thread",
            WindowPolicy(), ApiContextLimits(32000, 2000), object())
        with self.assertRaisesRegex(ValueError, "Host ceiling"):
            factory._budgeted_client(mode, permissive, profile)
        self.assertIs(factory._budgeted_client(mode, guarded, profile), guarded)

    async def test_summary_has_its_own_cap_and_shared_accounting(self):
        class Guard:
            accounts_task_usage = True
            def stream_for(self, purpose, system, messages, tools, *, constraints):
                self.purpose, self.constraints = purpose, constraints
                async def events():
                    yield ModelEvent(ModelEventKind.TEXT_DELTA, text="summary")
                    yield ModelEvent(ModelEventKind.USAGE, usage=Usage(12, 4))
                    yield ModelEvent(ModelEventKind.COMPLETED)
                return events()
        class Sessions:
            async def load_task_for_thread(self, thread):
                return SimpleNamespace(id="task")
            async def consume_task_usage(self, *args):
                raise AssertionError("shared guard must be charged once")
        guard = Guard()
        source = anchor_message("thread", 1, Message("user", "Inspect code"))
        request = SummaryRequest((source,), 100, 1000)
        response = await ModelSemanticSummarizer(guard, "model", Sessions()).summarize(
            request, CancellationToken())
        self.assertEqual(response.summary, "summary")
        self.assertEqual(guard.purpose, "semantic_summary")
        self.assertEqual(guard.constraints, RequestBudgetConstraints(
            auxiliary_input_tokens=900, auxiliary_total_tokens=1000))


if __name__ == "__main__":
    unittest.main()
