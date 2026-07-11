from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.task_state import render_task_state  # noqa: E402
from code_agent.context.tokens import estimate_tokens  # noqa: E402
from code_agent.core.task_state import CommandFact, TaskState  # noqa: E402


class RenderTaskStateTests(unittest.TestCase):
    def test_renderer_preserves_high_priority_facts_before_lower_priority_facts(self) -> None:
        state = TaskState(
            objective="Repair startup regression",
            failed_commands=(CommandFact("python -m unittest", 1, "command failed"),),
            files_changed=("src/app.py",),
            open_questions=("Does Windows need a fallback?",),
            verified_facts=("low priority " * 30,),
            working_notes=("unverified note " * 30,),
            files_read=("src/large.py",),
        )

        rendered = render_task_state(state, token_budget=100)

        self.assertLessEqual(estimate_tokens(rendered), 100)
        self.assertIn("Repair startup regression", rendered)
        self.assertIn("python -m unittest", rendered)
        self.assertIn("src/app.py", rendered)
        self.assertIn("Does Windows need a fallback?", rendered)
        self.assertNotIn("low priority", rendered)
        self.assertNotIn("unverified note", rendered)

    def test_renderer_labels_working_notes_as_unverified(self) -> None:
        rendered = render_task_state(
            TaskState(working_notes=("try the fallback",)), token_budget=100
        )

        self.assertIn(
            "Task state (facts are verified; working notes are unverified):", rendered
        )
        self.assertIn("Working notes (unverified):", rendered)


if __name__ == "__main__":
    unittest.main()
