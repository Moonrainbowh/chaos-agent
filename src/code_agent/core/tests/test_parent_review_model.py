"""Request routing, telemetry and accounting share a frozen review binding."""
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code_agent.core._engine_run import _RunState, _TurnState
from code_agent.core.cancellation import CancellationToken
from code_agent.core.engine import AgentEngine
from code_agent.core.errors import ModelStreamError
from code_agent.core.events import EventKind
from code_agent.core.limits import EngineLimits, TaskBudget
from code_agent.core.models import ModelEvent, ModelEventKind, Usage
from code_agent.core.parent_review import ParentReviewSnapshot
from code_agent.core.parent_review_model import ParentReviewModel
from code_agent.core.tests._engine_support import (
    FakeActionDispatcher, FakeContextBuilder, FakeModelClient, MemorySessionRepository,
)


IDENTITY = {"profile": "review", "model": "review-model", "parameters": {"tags": ["a", "b"]}}


class ParentReviewModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.usage = Usage(input_tokens=7, output_tokens=3)
        stream = [ModelEvent(ModelEventKind.TEXT_DELTA, text="PRIVATE_REVIEW"),
                  ModelEvent(ModelEventKind.USAGE, usage=self.usage),
                  ModelEvent(ModelEventKind.COMPLETED)]
        self.base = FakeModelClient([stream] * 8)
        self.review = FakeModelClient([stream] * 8)
        self.context = FakeContextBuilder()
        self.actions = FakeActionDispatcher()
        self.sessions = MemorySessionRepository()
        self.thread_id = await self.sessions.create_thread()
        self.binding = ParentReviewModel(self.review, "review-model", copy.deepcopy(IDENTITY))
        self.engine = AgentEngine(self.base, self.context, self.actions, self.sessions,
                                  model_name="base-model", parent_review_model=self.binding)
        self.engine._journal.consume_task_usage = AsyncMock()
        self.engine._journal.mark_task_budget_warnings = AsyncMock(return_value=())

    def state(self, phase="independent", *, bound=True, task=True):
        data = {"phase": phase, "objective": "Review source", "requirements": [],
                "sources": [], "advisory": "CHILD_SECRET", "child_objective": "CHILD_SECRET"}
        if bound:
            data["review_model"] = copy.deepcopy(IDENTITY)
        return _RunState(self.thread_id, CancellationToken(),
                         SimpleNamespace(id="task", contract=SimpleNamespace(interaction_mode="code")) if task else None,
                         TaskBudget("base-model", EngineLimits(), model_turns=1), None,
                         parent_review=ParentReviewSnapshot(data))

    async def request(self, state, number=1):
        turn = _TurnState(number, (), set())
        bundles = []
        events = [event async for event in self.engine._start_turn(state, turn, "inspect", bundles)]
        events += [event async for event in self.engine._stream_model_events(state, turn, bundles[0])]
        return events

    async def test_independent_comparison_and_both_repairs_use_review_client(self):
        for phase, repair in (("independent", False), ("comparison", False),
                              ("independent", True), ("comparison", True)):
            with self.subTest(phase=phase, repair=repair):
                state = self.state(phase)
                if repair:
                    state.parent_review.data.update(repair=[{"path": "/unknowns", "reason": "missing"}],
                                                    repair_response="{}", repair_phase=phase)
                events = await self.request(state)
                self.assertEqual([e.payload["model"] for e in events if e.kind is EventKind.MODEL_STARTED],
                                 ["review-model"])
                self.assertEqual(state.total_usage.total_tokens, 10)
                self.assertFalse(any(e.kind is EventKind.MODEL_EVENT and
                                     e.payload["event"].get("text") == "PRIVATE_REVIEW" for e in events))
        self.assertEqual(len(self.review.calls), 4)
        self.assertEqual(self.base.calls, [])
        self.assertEqual(self.context.calls, [])
        self.assertEqual(self.actions.requests, [])
        self.assertTrue(all(call[2] == () for call in self.review.calls))
        self.assertNotIn("CHILD_SECRET", self.review.calls[0][1][0].content)
        self.assertEqual(self.engine._journal.consume_task_usage.await_count, 4)
        self.assertIs(self.engine._model, self.base)
        self.assertEqual(self.engine._model_name, "base-model")

    async def test_bound_config_missing_drifted_or_malformed_has_no_started_or_provider(self):
        for identity, binding in ((IDENTITY, None), ({"model": "changed"}, self.binding),
                                  (None, self.binding), ({"parameters": object()}, self.binding)):
            with self.subTest(identity=identity, binding=binding):
                state = self.state()
                state.parent_review.data["review_model"] = identity
                self.engine._parent_review_model = binding
                with self.assertRaises(ModelStreamError):
                    await self.request(state)
        self.assertEqual(self.sessions.events[self.thread_id], [])
        self.assertEqual(self.base.calls, [])
        self.assertEqual(self.review.calls, [])
        self.engine._journal.consume_task_usage.assert_not_awaited()

    async def test_legacy_snapshot_without_binding_uses_base_with_injection_present(self):
        events = await self.request(self.state(bound=False))
        self.assertEqual(len(self.base.calls), 1)
        self.assertEqual(self.review.calls, [])
        self.assertEqual([e.payload["model"] for e in events if e.kind is EventKind.MODEL_STARTED], ["base-model"])

    async def test_normal_and_child_requests_use_base_without_review_leakage(self):
        for task in (True, False):
            state = self.state(task=task)
            state.parent_review = None
            events = await self.request(state)
            self.assertEqual([e.payload["model"] for e in events if e.kind is EventKind.MODEL_STARTED], ["base-model"])
            self.assertEqual(state.total_usage.total_tokens, 10)
        self.assertEqual(len(self.base.calls), 2)
        self.assertEqual(self.review.calls, [])
        self.assertEqual(len(self.context.calls), 2)
        self.assertEqual(self.engine._journal.consume_task_usage.await_count, 1)

    async def test_accounting_uses_actual_selected_client_and_never_double_counts(self):
        self.base.accounts_task_usage = False
        self.review.accounts_task_usage = True
        state = self.state()
        await self.request(state)
        self.engine._journal.consume_task_usage.assert_not_awaited()
        self.assertEqual(state.total_usage.total_tokens, 10)
        self.engine._journal.mark_task_budget_warnings.assert_awaited_once_with("task")
        self.base.accounts_task_usage = True
        self.review.accounts_task_usage = False
        await self.request(self.state())
        self.engine._journal.consume_task_usage.assert_awaited_once_with("task", self.usage)

    async def test_started_request_keeps_same_client_for_stream_and_usage(self):
        self.review.accounts_task_usage = True
        state = self.state()
        turn = _TurnState(1, (), set())
        bundles = []
        events = [event async for event in self.engine._start_turn(state, turn, "inspect", bundles)]
        self.assertEqual([e.payload["model"] for e in events if e.kind is EventKind.MODEL_STARTED], ["review-model"])
        self.engine._parent_review_model = None
        state.parent_review.data["review_model"] = {"model": "changed-after-start"}
        _ = [event async for event in self.engine._stream_model_events(state, turn, bundles[0])]
        self.assertEqual(len(self.review.calls), 1)
        self.assertEqual(self.base.calls, [])
        self.engine._journal.consume_task_usage.assert_not_awaited()
        self.assertEqual(state.total_usage.total_tokens, 10)

    def test_identity_is_deeply_frozen_and_canonicalizes_nested_lists(self):
        supplied = copy.deepcopy(IDENTITY)
        binding = ParentReviewModel(self.review, "review-model", supplied)
        supplied["parameters"]["tags"].append("changed")
        self.assertTrue(binding.matches(IDENTITY))
        self.assertTrue(binding.matches({"parameters": {"tags": ("a", "b")},
                                         "model": "review-model", "profile": "review"}))
        self.assertFalse(binding.matches(supplied))
        with self.assertRaises(TypeError):
            binding.identity["parameters"]["tags"] = []
        numeric = ParentReviewModel(self.review, "review-model", {"value": 1})
        self.assertFalse(numeric.matches({"value": True}))

    def test_inactive_or_delivered_snapshot_does_not_route_review(self):
        for snapshot in (ParentReviewSnapshot(), ParentReviewSnapshot({"phase": "delivered", "review_model": IDENTITY})):
            state = self.state()
            state.parent_review = snapshot
            self.assertEqual(self.engine._select_request_model(state), (self.base, "base-model"))


if __name__ == "__main__":
    unittest.main()
