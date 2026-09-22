from __future__ import annotations

import unittest
from code_agent.core.engine_turn_feedback import (
    call_signature,
    duplicate_failed_call_result,
    format_action_error,
)
from code_agent.core.models import ActionResult, ToolCall


class DuplicateFailedCallTests(unittest.TestCase):
    def test_duplicate_failed_call_returns_none_initially(self):
        call = ToolCall("1", "run_command", {"command": "Get-ChildItem"})
        result = duplicate_failed_call_result(None, call)
        self.assertIsNone(result)

    def test_duplicate_failed_call_blocks_identical_failure(self):
        call = ToolCall("1", "run_command", {"command": "Get-ChildItem", "extra": "1"})
        last_failed = (
            call_signature(call),
            "invalid tool arguments: unexpected argument(s): extra. Allowed arguments: command, description.",
        )
        result = duplicate_failed_call_result(last_failed, call)
        self.assertIsNotNone(result)
        self.assertTrue(result.is_error)
        self.assertIn("The identical tool call failed on the previous attempt", str(result.output))
        self.assertIn("unexpected argument(s): extra", str(result.output))
        self.assertIn("Do not repeat the same call unchanged", str(result.output))

    def test_modified_arguments_are_not_blocked(self):
        failed_call = ToolCall("1", "run_command", {"command": "Get-ChildItem", "extra": "1"})
        last_failed = (
            call_signature(failed_call),
            "some error",
        )
        corrected_call = ToolCall("2", "run_command", {"command": "Get-ChildItem"})
        result = duplicate_failed_call_result(last_failed, corrected_call)
        self.assertIsNone(result)

    def test_format_action_error_extracts_detail_and_error(self):
        result_dict = {
            "output": {
                "error": "invalid tool arguments",
                "detail": "unexpected argument(s): foo",
            }
        }
        formatted = format_action_error(result_dict)
        self.assertEqual(formatted, "invalid tool arguments: unexpected argument(s): foo")


if __name__ == "__main__":
    unittest.main()

