from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.engine_turn_feedback import (  # noqa: E402
    call_signature,
    duplicate_failed_call_result,
    format_action_error,
    is_validation_failure,
)
from code_agent.core.exploration_repeat import ToolOnlyConvergenceGuard  # noqa: E402
from code_agent.core.models import ActionRequest, ActionResult, ToolCall  # noqa: E402
from code_agent_win.action_support import preflight_action  # noqa: E402
from code_agent_win.tools import validate_tool_arguments  # noqa: E402


class AgentArgumentSelfCorrectionTests(unittest.TestCase):
    """End-to-end verification for tool parameter validation, retry protection,
    and exploration quota preservation as requested by user."""

    def test_full_argument_failure_and_self_correction_cycle(self):
        guard = ToolOnlyConvergenceGuard(warn_at=3, force_at=5, max_correction_failures=3)

        # 1. First 3 turns: normal read-only exploration
        read_call = ToolCall("c1", "read_file", {"path": "README"})
        for _ in range(3):
            obs = guard.observe(has_text=False, calls=[read_call], has_validation_error=False)
        self.assertEqual(obs.kind, "warn")
        self.assertEqual(guard.exploration_count, 3)

        # 2. Turn 4: Agent makes an invalid tool call with an unexpected argument
        invalid_req = ActionRequest("c4", "run_command", {"command": "Get-ChildItem", "bogus_arg": "invalid"})
        err_res = preflight_action(invalid_req, invalid_req)
        self.assertIsNotNone(err_res)
        self.assertTrue(err_res.is_error)
        self.assertTrue(is_validation_failure(err_res))
        
        # Verify the error is actionable and lists both bad arg and allowed args
        error_msg = format_action_error(err_res.to_dict())
        self.assertIn("unexpected argument(s): bogus_arg", error_msg)
        self.assertIn("Allowed arguments:", error_msg)
        self.assertIn("command", error_msg)
        self.assertIn("description", error_msg)

        # Record failed call state
        last_failed_call = (call_signature(ToolCall("c4", invalid_req.name, invalid_req.arguments)), error_msg)

        # Feed Turn 4 into ToolOnlyConvergenceGuard:
        # Exploration quota MUST NOT advance to 4 or 5!
        obs_t4 = guard.observe(has_text=False, calls=[ToolCall("c4", invalid_req.name, invalid_req.arguments)], has_validation_error=True)
        self.assertIsNone(obs_t4)
        self.assertEqual(guard.exploration_count, 3, "Exploration count must NOT increase on validation error")
        self.assertEqual(guard.correction_count, 1)

        # 3. Turn 5: If agent blindly repeats the identical call unchanged,
        # circuit breaker intercepts it with actionable feedback
        identical_call = ToolCall("c5", invalid_req.name, invalid_req.arguments)
        blocked_res = duplicate_failed_call_result(last_failed_call, identical_call)
        self.assertIsNotNone(blocked_res)
        self.assertTrue(blocked_res.is_error)
        self.assertEqual(blocked_res.output.get("error_code"), "duplicate_failed_call_blocked")
        self.assertIn("The identical tool call failed on the previous attempt", str(blocked_res.output))
        self.assertIn("unexpected argument(s): bogus_arg", str(blocked_res.output))

        # 4. Turn 6: Agent self-corrects by removing the bad argument (or using optional description)
        corrected_req = ActionRequest(
            "c6",
            "run_command",
            {"command": "Get-ChildItem", "description": "List files in current directory"},
        )
        preflight_ok = preflight_action(corrected_req, corrected_req)
        self.assertIsNone(preflight_ok, "Self-corrected call with description must pass validation")

        corrected_call = ToolCall("c6", corrected_req.name, corrected_req.arguments)
        dup_check = duplicate_failed_call_result(last_failed_call, corrected_call)
        self.assertIsNone(dup_check, "Modified call must NOT be blocked by duplicate call detector")

        # Simulate successful completion of Turn 6:
        last_failed_call = None
        obs_t6 = guard.observe(has_text=False, calls=[corrected_call], has_validation_error=False)
        self.assertIsNone(obs_t6)
        self.assertEqual(guard.correction_count, 0, "Correction count must reset on success")
        self.assertEqual(guard.exploration_count, 4, "Normal exploration now resumes at turn 4")


if __name__ == "__main__":
    unittest.main()

