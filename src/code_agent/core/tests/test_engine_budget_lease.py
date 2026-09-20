from __future__ import annotations

import json
import unittest

from code_agent.core._engine_run import _message_fingerprint
from code_agent.core.limits import (
    BudgetLeaseTier,
    BudgetReservation,
    BudgetReserveStatus,
    EngineLimits,
    TaskBudget,
)
from code_agent.core.models import ActionResult, Message, ToolCall


class EngineBudgetLeaseTests(unittest.TestCase):
    def test_tool_result_fingerprint_ignores_call_id(self) -> None:
        first = _tool_message("call-1", {"content": "same"})
        repeated = _tool_message("call-2", {"content": "same"})
        changed = _tool_message("call-3", {"content": "different"})

        self.assertEqual(_message_fingerprint(first), _message_fingerprint(repeated))
        self.assertNotEqual(_message_fingerprint(first), _message_fingerprint(changed))

    def test_tool_result_fingerprint_includes_normalized_arguments(self) -> None:
        first = _tool_message("call-1", {"content": "same"})
        second = _tool_message("call-2", {"content": "same"})
        first_request = Message(
            role="assistant",
            tool_calls=(ToolCall("call-1", "read_file", {"path": "a.py"}),),
        )
        second_request = Message(
            role="assistant",
            tool_calls=(ToolCall("call-2", "read_file", {"path": "b.py"}),),
        )

        self.assertNotEqual(
            _message_fingerprint(first, (first_request, first)),
            _message_fingerprint(second, (second_request, second)),
        )

    def test_tool_result_fingerprint_serializes_nested_frozen_search_results(self) -> None:
        message = _tool_message(
            "call-1",
            {
                "matches": [
                    {
                        "path": "src/data_pipeline.py",
                        "line": 12,
                        "captures": [{"text": "dynamic_features"}],
                    }
                ]
            },
            name="search_text",
        )
        request = Message(
            role="assistant",
            tool_calls=(
                ToolCall(
                    "call-1",
                    "search_text",
                    {"query": "dynamic", "include_globs": ["src/**/*.py"]},
                ),
            ),
        )

        fingerprint = _message_fingerprint(message, (request, message))

        self.assertEqual(len(fingerprint), 64)
        self.assertTrue(all(character in "0123456789abcdef" for character in fingerprint))

    def test_lease_exhaustion_is_distinct_from_hard_exhaustion(self) -> None:
        budget = TaskBudget(
            "model",
            EngineLimits(max_agent_rounds=50, max_tool_calls=128),
            lease_tier=BudgetLeaseTier.QUICK,
            lease_model_turn_limit=4,
            lease_tool_call_limit=8,
        )

        reservation = BudgetReservation(
            budget,
            BudgetReserveStatus.LEASE_EXHAUSTED,
            "no new trusted progress",
        )

        self.assertFalse(reservation.accepted)
        self.assertIs(reservation.status, BudgetReserveStatus.LEASE_EXHAUSTED)


def _tool_message(
    call_id: str, output: dict[str, object], *, name: str = "read_file"
) -> Message:
    result = ActionResult(call_id, name, output)
    return Message(
        role="tool",
        name=name,
        tool_call_id=call_id,
        content=json.dumps(result.to_dict()),
    )


if __name__ == "__main__":
    unittest.main()
