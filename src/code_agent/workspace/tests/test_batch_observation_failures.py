from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace import _batch_apply  # noqa: E402
from code_agent.workspace.edits import BatchApplyStatus  # noqa: E402
from code_agent.workspace.tests._edit_test_support import (  # noqa: E402
    WorkspaceEditorTestCase,
)


class BatchPostObservationFailureTests(WorkspaceEditorTestCase):
    def test_post_observe_error_rolls_back_prior_proven_operation(self) -> None:
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes(b"first-before")
        second.write_bytes(b"second-before")
        plan = self.editor.plan_batch(
            (
                self.editor.plan_write("first.txt", "first-after"),
                self.editor.plan_write("second.txt", "second-after"),
            )
        )
        real_execute = _batch_apply._execute_operation
        real_observe = _batch_apply.observe
        calls = 0
        fail_post_observe = False

        def execute_then_fail_observe(*args, **kwargs):
            nonlocal calls, fail_post_observe
            calls += 1
            result = real_execute(*args, **kwargs)
            if calls == 2:
                fail_post_observe = True
            return result

        def observe_once(editor, relative):
            nonlocal fail_post_observe
            if fail_post_observe and relative == "second.txt":
                fail_post_observe = False
                raise OSError("injected post-observation failure")
            return real_observe(editor, relative)

        with patch.object(
            _batch_apply,
            "_execute_operation",
            side_effect=execute_then_fail_observe,
        ), patch.object(_batch_apply, "observe", side_effect=observe_once):
            result = self.editor.apply_batch(plan)

        self.assertEqual(result.status, BatchApplyStatus.PARTIAL_CONFLICT)
        self.assertEqual(first.read_bytes(), b"first-before")
        self.assertEqual(second.read_bytes(), b"second-after")
        self.assertEqual(result.rolled_back_operations, (0,))


if __name__ == "__main__":
    unittest.main()
