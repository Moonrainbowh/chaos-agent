from __future__ import annotations

import unittest
import asyncio
from types import SimpleNamespace

from code_agent.core.exploration_repeat import (
    ExplorationRepeatObserver,
    ToolOnlyConvergenceGuard,
)
from code_agent.core.models import ActionResult, ToolCall, Message
from code_agent.core._engine_turn import AgentEngineTurnMixin
from code_agent.core._engine_convergence import AgentEngineConvergenceMixin
from code_agent.core._engine_run import _TurnState
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.engine_turn_feedback import circuit_breaker_result


def _result(call: ToolCall, output: object, *, error: bool = False) -> ActionResult:
    return ActionResult(call.id, call.name, output, error)


class ExplorationRepeatObserverTests(unittest.TestCase):
    def test_circuit_breaker_serializes_frozen_nested_arguments(self):
        call = ToolCall("1", "write_file", {
            "path": "a.txt",
            "operations": [{"kind": "replace", "value": "x"}],
        })
        history = []
        self.assertIsNone(circuit_breaker_result(history, call))
        self.assertIsNone(circuit_breaker_result(history, call))
        result = circuit_breaker_result(history, call)
        self.assertIsNotNone(result)
        self.assertTrue(result.is_error)

    def test_changed_file_content_is_new_result(self):
        observer = ExplorationRepeatObserver()
        call = ToolCall("1", "read_file", {"path": "a.txt"})
        self.assertIsNone(observer.observe(call, _result(call, {"content": "old"})))
        self.assertIsNone(observer.observe(call, _result(call, {"content": "new"})))

    def test_different_ranges_and_files_do_not_merge(self):
        observer = ExplorationRepeatObserver()
        self.assertIsNone(observer.observe(ToolCall("1", "read_file", {"path": "a", "start": 0}), _result(ToolCall("1", "read_file", {}), {"x": 1})))
        self.assertIsNone(observer.observe(ToolCall("2", "read_file", {"path": "a", "start": 10}), _result(ToolCall("2", "read_file", {}), {"x": 1})))
        self.assertIsNone(observer.observe(ToolCall("3", "read_file", {"path": "b"}), _result(ToolCall("3", "read_file", {}), {"x": 1})))

    def test_ab_interleaving_accumulates_per_signature(self):
        observer = ExplorationRepeatObserver()
        a = ToolCall("a", "read_file", {"path": "a"})
        b = ToolCall("b", "read_file", {"path": "b"})
        ra, rb = _result(a, {"content": "same"}), _result(b, {"content": "same"})
        self.assertIsNone(observer.observe(a, ra))
        self.assertIsNone(observer.observe(b, rb))
        self.assertEqual(observer.observe(a, ra).kind, "warn")
        self.assertEqual(observer.observe(b, rb).kind, "warn")
        self.assertEqual(observer.observe(a, ra).kind, "pause")

    def test_repeated_error_is_still_stagnation_and_metadata_is_ignored(self):
        observer = ExplorationRepeatObserver()
        call = ToolCall("1", "search_text", {"pattern": "startup"})
        first = _result(call, {"error": "not found", "duration_ms": 1, "request_id": "1"}, error=True)
        second = _result(call, {"error": "not found", "duration_ms": 99, "request_id": "2"}, error=True)
        self.assertIsNone(observer.observe(call, first))
        self.assertEqual(observer.observe(call, second).kind, "warn")

    def test_other_tools_are_not_observed(self):
        observer = ExplorationRepeatObserver()
        call = ToolCall("1", "write_file", {"path": "a"})
        for _ in range(5):
            self.assertIsNone(observer.observe(call, _result(call, {"changed": False})))

    def test_metadata_operation_does_not_clear_read_history(self):
        observer = ExplorationRepeatObserver()
        read = ToolCall("1", "read_file", {"path": "a"})
        meta = ToolCall("2", "load_tool_contract", {"name": "x"})
        result = _result(read, {"content": "same"})
        self.assertIsNone(observer.observe(read, result))
        self.assertIsNone(observer.observe(meta, _result(meta, {"ok": True})))
        self.assertEqual(observer.observe(read, result).kind, "warn")

    def test_controlled_pause_is_safe_in_task_and_normal_modes(self):
        for task in (None, SimpleNamespace(id="task-1")):
            host = _DispatchHost()
            state = SimpleNamespace(thread_id="t", used_call_ids=set(), messages=(),
                                    action_history=[], exploration_repeat=ExplorationRepeatObserver(),
                                    task=task, supervisor=object(), stop_requested=False, token=None)
            async def run():
                all_events = []
                for index in range(3):
                    call = ToolCall(str(index), "read_file", {"path": "same.txt"})
                    turn = SimpleNamespace(tool_names={"read_file"})
                    all_events.extend([event async for event in AgentEngineTurnMixin._dispatch_turn_call(host, state, turn, call, [False])])
                return all_events
            events = asyncio.run(run())
            kinds = [event.kind for event in events]
            self.assertEqual(kinds.count(EventKind.ACTION_STARTED), 3)
            self.assertEqual(kinds.count(EventKind.ACTION_COMPLETED), 3)
            self.assertEqual(kinds.count(EventKind.MESSAGE_ADDED), 3)
            self.assertEqual(kinds.count(EventKind.TASK_BUDGET_WARNING), 2)
            self.assertEqual(kinds.count(EventKind.TASK_PAUSED if task is not None else EventKind.ERROR), 1)
            self.assertTrue(state.stop_requested)


class ToolOnlyConvergenceGuardTests(unittest.TestCase):
    def test_task_warns_then_requests_evidence_finalization_for_tool_only_turns(self):
        guard = ToolOnlyConvergenceGuard()
        read = ToolCall("read", "read_file", {"path": "x.py"})
        self.assertIsNone(guard.observe(has_text=False, calls=[read]))
        self.assertIsNone(guard.observe(has_text=False, calls=[read]))
        warning = guard.observe(has_text=False, calls=[read])
        self.assertEqual(warning.kind, "warn")
        self.assertIsNone(guard.observe(has_text=False, calls=[read]))
        finalization = guard.observe(has_text=False, calls=[read])
        self.assertEqual(finalization.kind, "finalize")

    def test_only_host_progress_resets_the_stagnation_counter(self):
        guard = ToolOnlyConvergenceGuard()
        read = ToolCall("read", "read_file", {"path": "x.py"})
        write = ToolCall("write", "write_file", {"path": "x.py", "content": "x"})
        verify = ToolCall("verify", "run_verification", {"recipe": "unit"})
        new_context = ToolCall("window", "new_context", {})
        guard.observe(has_text=False, calls=[read])
        self.assertIsNone(guard.observe(has_text=True, calls=[read], has_host_progress=True))
        self.assertIsNone(guard.observe(has_text=False, calls=[read]))
        self.assertIsNone(guard.observe(has_text=False, calls=[write], has_host_progress=True))
        self.assertIsNone(guard.observe(has_text=False, calls=[verify], has_host_progress=True))
        guard.observe(has_text=False, calls=[read])
        guard.observe(has_text=False, calls=[read])
        self.assertEqual(guard.observe(has_text=False, calls=[new_context]).kind, "warn")
        self.assertIsNone(guard.observe(has_text=True, calls=[read]))
        self.assertEqual(
            guard.observe(has_text=False, calls=[read]).kind,
            "finalize",
        )
        guard.reset()
        self.assertIsNone(guard.observe(has_text=False, calls=[read]))

    def test_validation_error_does_not_consume_exploration_quota(self):
        guard = ToolOnlyConvergenceGuard(warn_at=3, force_at=5, max_correction_failures=3)
        read = ToolCall("read", "read_file", {"path": "x.py"})
        cmd = ToolCall("cmd", "run_command", {"command": "dir"})

        # 3 normal read exploration turns:
        self.assertIsNone(guard.observe(has_text=False, calls=[read]))
        self.assertIsNone(guard.observe(has_text=False, calls=[read]))
        warn = guard.observe(has_text=False, calls=[read])
        self.assertEqual(warn.kind, "warn")
        self.assertEqual(guard.exploration_count, 3)

        # 2 validation error turns: exploration_count must NOT advance to 5!
        self.assertIsNone(guard.observe(has_text=False, calls=[cmd], has_validation_error=True))
        self.assertEqual(guard.exploration_count, 3)
        self.assertEqual(guard.correction_count, 1)

        self.assertIsNone(guard.observe(has_text=False, calls=[cmd], has_validation_error=True))
        self.assertEqual(guard.exploration_count, 3)
        self.assertEqual(guard.correction_count, 2)

        # 3rd consecutive validation error triggers finalize on correction failures
        finalize = guard.observe(has_text=False, calls=[cmd], has_validation_error=True)
        self.assertIsNotNone(finalize)
        self.assertEqual(finalize.kind, "finalize")
        self.assertIn("repeated tool argument/contract validation errors", finalize.reason)

    def test_successful_turn_resets_correction_count(self):
        guard = ToolOnlyConvergenceGuard(warn_at=3, force_at=5, max_correction_failures=3)
        cmd = ToolCall("cmd", "run_command", {"command": "dir"})
        read = ToolCall("read", "read_file", {"path": "x.py"})

        self.assertIsNone(guard.observe(has_text=False, calls=[cmd], has_validation_error=True))
        self.assertIsNone(guard.observe(has_text=False, calls=[cmd], has_validation_error=True))
        self.assertEqual(guard.correction_count, 2)

        # Next turn succeeds without validation error: correction_count resets to 0
        self.assertIsNone(guard.observe(has_text=False, calls=[read], has_validation_error=False))
        self.assertEqual(guard.correction_count, 0)
        self.assertEqual(guard.exploration_count, 1)


class _DispatchHost:
    def __init__(self):
        self._journal = self
        self.paused = False

    async def append_event(self, thread_id, event):
        pass

    def _track_turn_event(self, state, event, validation_blocked):
        if event.kind is EventKind.MESSAGE_ADDED:
            state.messages += (Message.from_dict(event.payload["message"]),)

    async def _pause_task(self, thread_id, task, supervisor, reason):
        self.paused = True

    async def _dispatch(self, thread_id, call, token, **kwargs):
        request = {"id": call.id, "name": call.name, "arguments": dict(call.arguments)}
        yield AgentEvent(EventKind.ACTION_REQUESTED, {"request": request})
        yield AgentEvent(EventKind.ACTION_STARTED, {"request_id": call.id, "name": call.name})
        result = ActionResult(call.id, call.name, {"content": "same"})
        yield AgentEvent(EventKind.ACTION_COMPLETED, {"result": result.to_dict()})
        message = Message(role="tool", name=call.name, tool_call_id=call.id, content="{}")
        yield AgentEvent(EventKind.MESSAGE_ADDED, {"message": message.to_dict()})


class _ConvergenceJournal:
    async def append_event(self, thread_id, event):
        pass

    async def append_message(self, thread_id, message):
        pass

    def message_added(self, message):
        return AgentEvent(EventKind.MESSAGE_ADDED, {"message": message.to_dict()})


class ConvergenceMixinTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_answer_skips_forced_summary_turn(self):
        host = AgentEngineConvergenceMixin()
        host._journal = _ConvergenceJournal()
        state = SimpleNamespace(
            stop_requested=False,
            tool_only_guard=ToolOnlyConvergenceGuard(),
            thread_id="thread-1",
            has_user_visible_answer=True,
            summary_required=False,
            pending_runtime_notices=[],
            messages=(),
            task=None,
        )
        turn = _TurnState(
            number=1,
            tools=(),
            tool_names=set(),
            text_parts=[],
            calls=[ToolCall("read", "read_file", {"path": "answer.md"})],
        )

        for _ in range(5):
            [event async for event in host._observe_tool_only_convergence(state, turn)]

        self.assertTrue(state.stop_requested)
        self.assertFalse(state.summary_required)
