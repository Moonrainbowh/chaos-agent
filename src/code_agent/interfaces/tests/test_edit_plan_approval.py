from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionResult
from code_agent.interfaces.action_summary import action_summary
from code_agent.interfaces.approval import (
    ApprovalBroker,
    ApprovalRequest,
    EditPlanApprovalView,
)
from code_agent.interfaces.tui_interactions import TuiInteractions


_DIGEST = "b" * 64
_DIFF = """--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-old
+new
"""


def view() -> EditPlanApprovalView:
    return EditPlanApprovalView(
        "plan-1",
        _DIGEST,
        ("replace a.py", "move b.py -> pkg/b.py"),
        ("dirty_baseline", "untracked_existing", "move"),
        ("a.py", "b.py", "pkg/b.py"),
        _DIFF,
        False,
    )


class EditPlanApprovalModelTests(unittest.TestCase):
    def test_view_is_strict_and_attaches_only_as_a_typed_value(self) -> None:
        preview = view()
        request = ApprovalRequest(
            "request",
            "apply_workspace_edit_plan_v1",
            {"plan_id": "plan-1", "plan_digest": _DIGEST},
            edit_plan=preview,
        )
        self.assertIs(request.edit_plan, preview)
        self.assertEqual(
            EditPlanApprovalView(
                "plan-2",
                _DIGEST,
                ("write existing.txt",),
                ("non_git_existing",),
                ("existing.txt",),
                _DIFF,
                False,
            ).risk_flags,
            ("non_git_existing",),
        )
        with self.assertRaises(ValueError):
            EditPlanApprovalView(
                "plan-1", _DIGEST.upper(), ("write a.py",), (), ("a.py",), "", False
            )
        with self.assertRaises(TypeError):
            ApprovalRequest(
                "request", "apply_workspace_edit_plan_v1", {},
                edit_plan={"combined_diff": _DIFF},  # type: ignore[arg-type]
            )
        with self.assertRaises(ValueError):
            ApprovalRequest(
                "request",
                "apply_workspace_edit_plan_v1",
                {"plan_id": "plan-other", "plan_digest": _DIGEST},
                edit_plan=preview,
            )
        with self.assertRaises(ValueError):
            ApprovalRequest(
                "request",
                "write_file",
                {"path": "a.py"},
                edit_plan=preview,
            )

    def test_plan_action_summary_is_bounded_and_specific(self) -> None:
        result = ActionResult(
            "request",
            "plan_workspace_edits_v1",
            {
                "operations": ["replace a.py", "move b.py -> pkg/b.py"],
                "plan_id": "plan-1",
            },
            metadata={"operation_count": 2, "path_count": 3},
        )
        summary = action_summary(
            "plan_workspace_edits_v1",
            {"arguments": {"operations": []}},
            result,
        )
        self.assertIn("Plan workspace edits", summary)
        self.assertIn("2 operations", summary)
        self.assertIn("replace a.py", summary)


class EditPlanApprovalTuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_defaults_no_and_trusted_diff_opens_in_diff_view(self) -> None:
        broker = ApprovalBroker()
        request = ApprovalRequest(
            "request",
            "apply_workspace_edit_plan_v1",
            {"plan_id": "plan-1", "plan_digest": _DIGEST},
            "high",
            edit_plan=view(),
        )
        pending = asyncio.create_task(broker.request(request, CancellationToken()))
        app = SimpleNamespace(
            _pending_approval=await broker.next_request(),
            _pending_interaction=None,
            _rewind_flow=None,
            approvals=broker,
            _approval_done=asyncio.Event(),
            _columns=lambda: 100,
        )
        interactions = TuiInteractions()

        rows = interactions.rows(app)
        self.assertIn("plan-1", "\n".join(rows))
        self.assertIn("replace a.py", "\n".join(rows))
        self.assertIn("untracked_existing", "\n".join(rows))
        self.assertTrue(next(row for row in rows if "No" in row).startswith("› "))

        await interactions.handle_key(app, "d")
        self.assertTrue(interactions.approval_diff.active)
        self.assertEqual(interactions.approval_diff.view.current.path, "a.py")
        diff_rows = "\n".join(interactions.rows(app))
        self.assertIn("-old", diff_rows)
        self.assertIn("Esc back", diff_rows)
        self.assertNotIn("comment", diff_rows)
        self.assertNotIn("send", diff_rows)
        self.assertNotIn("refresh", diff_rows)

        await interactions.handle_key(app, "\x1b")
        self.assertFalse(interactions.approval_diff.active)
        self.assertIsNotNone(app._pending_approval)
        await interactions.handle_key(app, "\x1b")
        self.assertFalse(await pending)


if __name__ == "__main__":
    unittest.main()
