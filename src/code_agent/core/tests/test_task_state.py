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
            ActionRequest("apply-1", "apply_workspace_edit_plan_v1", {}),
            ActionResult(
                "apply-1",
                "apply_workspace_edit_plan_v1",
                {
                    "status": "applied",
                    "paths": ["src/app.py", "src/lib.py"],
                    "workspace_may_have_changed": True,
                },
            ),
        )
        state = reduce_task_state(
            state,
            ActionRequest("run-1", "run_command", {"command": "python -m test"}),
            ActionResult(
                "run-1",
                "run_command",
                {"stderr": "failed"},
                is_error=True,
                metadata={"execution_attempted": True, "returncode": 1},
            ),
        )

        self.assertEqual(state.files_read, ("src/app.py",))
        self.assertEqual(state.files_changed, ("src/app.py", "src/lib.py"))
        self.assertIn("Changed file: src/lib.py", state.verified_facts)
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

    def test_partial_edit_apply_records_possible_workspace_changes(self) -> None:
        request = ActionRequest("apply", "apply_workspace_edit_plan_v1", {})
        partial = ActionResult(
            "apply", request.name,
            {"workspace_may_have_changed": True, "paths": ["src/raced.py"]},
            is_error=True,
        )
        rolled_back = ActionResult(
            "apply", request.name,
            {"workspace_may_have_changed": False, "paths": ["src/clean.py"]},
            is_error=True,
        )

        changed = reduce_task_state(TaskState.empty(), request, partial)
        unchanged = reduce_task_state(changed, request, rolled_back)

        self.assertEqual(changed.files_changed, ("src/raced.py",))
        self.assertIn("Workspace may have changed: src/raced.py", changed.working_notes)
        self.assertIs(unchanged, changed)


if __name__ == "__main__":
    unittest.main()
