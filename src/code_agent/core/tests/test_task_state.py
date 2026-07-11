from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.models import ActionRequest, ActionResult  # noqa: E402
from code_agent.core.task_state import (  # noqa: E402
    CommandFact,
    TaskState,
    TaskStateUpdate,
    reduce_task_state,
)


class TaskStateTests(unittest.TestCase):
    def test_reducer_records_successful_read_write_and_failed_command(self) -> None:
        state = TaskState(objective="repair startup")
        state = reduce_task_state(
            state,
            ActionRequest("read-1", "read_file", {"path": "src/app.py"}),
            ActionResult("read-1", "read_file", {"text": "contents"}),
        )
        state = reduce_task_state(
            state,
            ActionRequest("write-1", "write_file", {"path": "src/app.py"}),
            ActionResult("write-1", "write_file", {"written": True}),
        )
        state = reduce_task_state(
            state,
            ActionRequest("run-1", "run_command", {"command": "python -m test"}),
            ActionResult(
                "run-1",
                "run_command",
                {"stderr": "failed"},
                is_error=True,
                metadata={"returncode": 1},
            ),
        )

        self.assertEqual(state.files_read, ("src/app.py",))
        self.assertEqual(state.files_changed, ("src/app.py",))
        self.assertEqual(
            state.failed_commands,
            (CommandFact("python -m test", 1, "command failed"),),
        )

    def test_values_are_immutable_json_safe_and_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "at most 32"):
            TaskState(files_read=tuple(str(index) for index in range(33)))
        with self.assertRaisesRegex(ValueError, "at most 1024"):
            CommandFact("x" * 1025, None, "failed")
        with self.assertRaises(TypeError):
            CommandFact("cmd", True, "failed")

        update = TaskStateUpdate(
            verified_facts=("tests passed",),
            working_notes=("check migration",),
            open_questions=("which version?",),
        )
        self.assertEqual(TaskStateUpdate.from_dict(update.to_dict()), update)
        state = TaskState(verified_facts=("tests passed",))
        self.assertEqual(TaskState.from_dict(state.to_dict()), state)


if __name__ == "__main__":
    unittest.main()
