from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.models import ActionRequest, ActionResult  # noqa: E402
from code_agent.core.task_state import CommandFact, TaskState, reduce_task_state  # noqa: E402


class CommandFactTests(unittest.TestCase):
    def test_rejected_commands_do_not_create_failed_facts(self) -> None:
        initial = TaskState(objective="run checks")
        for name, arguments in (
            ("run_command", {"command": "python -m unittest"}),
            ("run_process_v1", {"program": "python", "args": ["-m", "unittest"]}),
        ):
            with self.subTest(name=name):
                result = ActionResult(
                    "call", name, {"error": "approval required"}, is_error=True
                )
                self.assertEqual(
                    reduce_task_state(
                        initial, ActionRequest("call", name, arguments), result
                    ).failed_commands,
                    (),
                )

    def test_attempted_failures_create_bounded_auditable_facts(self) -> None:
        raw = "x" * 1_100
        cases = (
            (
                "run_command",
                {"command": raw},
                raw[:1_024],
            ),
            (
                "run_process_v1",
                {"program": "python", "args": ["-c", "raise SystemExit(7)"]},
                json.dumps(
                    ["python", "-c", "raise SystemExit(7)"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ),
        )
        for name, arguments, expected in cases:
            with self.subTest(name=name):
                updated = reduce_task_state(
                    TaskState(objective="run checks"),
                    ActionRequest("call", name, arguments),
                    ActionResult(
                        "call",
                        name,
                        {"stderr": "failed"},
                        is_error=True,
                        metadata={"execution_attempted": True, "returncode": 7},
                    ),
                )
                self.assertEqual(
                    updated.failed_commands,
                    (CommandFact(expected, 7, "command failed"),),
                )

    def test_attempted_success_does_not_create_failed_fact(self) -> None:
        updated = reduce_task_state(
            TaskState(objective="run checks"),
            ActionRequest(
                "call", "run_process_v1", {"program": "python", "args": []}
            ),
            ActionResult(
                "call",
                "run_process_v1",
                {"stdout": "ok"},
                metadata={"execution_attempted": True, "returncode": 0},
            ),
        )
        self.assertEqual(updated.failed_commands, ())


if __name__ == "__main__":
    unittest.main()
