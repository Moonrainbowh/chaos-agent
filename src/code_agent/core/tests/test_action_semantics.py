import unittest
from types import SimpleNamespace

from code_agent.core.action_semantics import operation_kind, resolve_supervision_call
from code_agent.core.engine_turn_feedback import (
    call_signature, circuit_breaker_result, duplicate_failed_call_result,
)
from code_agent.core.models import ActionRequest, ActionResult, ToolCall
from code_agent.core.exploration_repeat import ExplorationRepeatObserver


class ActionSemanticsTests(unittest.TestCase):
    def test_nested_slice_calls_and_results_are_actually_observed(self):
        observer = ExplorationRepeatObserver()
        call = ToolCall("slice", "read_code_slices", {
            "generation": 1, "targets": [{"path": "a.py", "start_line": 1}],
        })
        result = ActionResult(call.id, call.name, {
            "generation": 1, "slices": [{"path": "a.py", "text": "unchanged"}],
        })
        self.assertIsNone(observer.observe(call, result))
        self.assertEqual(observer.observe(call, result).kind, "warn")
        self.assertEqual(observer.observe(call, result).kind, "pause")

    def test_observation_resolver_retains_id_and_failure_uses_model_identity(self):
        call = ToolCall("original", "execute", {"operation": "command", "command": "x"})
        dispatcher = SimpleNamespace(resolve_supervision_action=lambda request:
            ActionRequest(request.id, "run_command", {"command": "x"}))
        observed = resolve_supervision_call(call, dispatcher)
        self.assertEqual(observed, ToolCall("original", "run_command", {"command": "x"}))
        failure = duplicate_failed_call_result(
            (call_signature(observed), "failed"), call, observed_call=observed)
        self.assertEqual((failure.request_id, failure.name), (call.id, call.name))

    def test_unknown_invalid_or_changed_id_does_not_become_progress(self):
        call = ToolCall("original", "write", {"operation": "unknown"})
        def invalid(request):
            raise ValueError("unknown operation")
        for resolver in (invalid, lambda request: ActionRequest("other", "write_file", {})):
            observed = resolve_supervision_call(call, SimpleNamespace(resolve_action=resolver))
            self.assertEqual(observed, call)
            self.assertEqual(operation_kind(observed), "other")

    def test_circuit_signature_is_shared_by_aliases_but_keeps_suboperations(self):
        legacy = ToolCall("one", "run_command", {"command": "x"})
        compact = ToolCall("two", "execute", {"operation": "command", "command": "x"})
        observed = ToolCall(compact.id, legacy.name, legacy.arguments)
        history = []
        self.assertIsNone(circuit_breaker_result(history, legacy))
        self.assertIsNone(circuit_breaker_result(history, compact, observed_call=observed))
        self.assertIsNone(circuit_breaker_result(history, ToolCall("other", "run_process_v1", {})))
        failure = circuit_breaker_result(history, compact, observed_call=observed)
        self.assertEqual(failure.name, "execute")
        self.assertEqual(failure.output["error_code"], "repeated_action_blocked")
