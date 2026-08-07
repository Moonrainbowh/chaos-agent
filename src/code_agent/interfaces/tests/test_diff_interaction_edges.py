from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from code_agent.interfaces._diff_parser import DiffScope
from code_agent.interfaces.diff_interaction import DiffInteraction, DiffMode
from code_agent.interfaces.diff_view import DiffView
from code_agent.interfaces.input_events import ExitGuard
from code_agent.interfaces.tui_input import handle_interrupt
from code_agent.interfaces.tui_interactions import TuiInteractions


def context_view(path: str = "x.py") -> DiffView:
    body = "".join(f" line-{index}\n" for index in range(8))
    patch = (
        f"--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,8 +1,8 @@\n{body}"
    )
    return DiffView.parse(patch, DiffScope.UNSTAGED, True)


def open_modal(view: DiffView | None = None) -> DiffInteraction:
    modal = DiffInteraction()
    modal.open(
        view or context_view(),
        scope=DiffScope.WORKING_TREE,
        recorded_diff=None,
    )
    return modal


class DiffCloseSafetyTests(unittest.TestCase):
    def test_ctrl_c_requires_confirmation_for_comment_draft(self) -> None:
        modal = open_modal()
        modal.handle_key("c")
        modal.handle_key("draft text")

        modal.request_close()

        self.assertTrue(modal.active)
        self.assertEqual(modal.mode, DiffMode.DISCARD)
        self.assertEqual(modal.editor.text, "draft text")

        modal.request_close()

        self.assertTrue(modal.active)
        self.assertEqual(modal.mode, DiffMode.COMMENT)
        self.assertEqual(modal.editor.text, "draft text")

    def test_second_ctrl_c_does_not_discard_saved_comments(self) -> None:
        modal = open_modal()
        for key in ("c", "saved note", "\r"):
            modal.handle_key(key)

        modal.request_close()
        modal.request_close()

        self.assertTrue(modal.active)
        self.assertEqual(modal.mode, DiffMode.BROWSE)
        self.assertTrue(modal.has_unsent_comments)


class DiffRenderSafetyTests(unittest.TestCase):
    def test_small_row_budget_keeps_selected_line_visible(self) -> None:
        modal = open_modal()
        for _ in range(5):
            modal.handle_key("down")

        rows = modal.rows(80, max_rows=6)

        self.assertLessEqual(len(rows), 6)
        self.assertTrue(any(row.startswith("›") for row in rows))

    def test_long_editor_keeps_cursor_visible(self) -> None:
        modal = open_modal()
        modal.handle_key("c")
        modal.handle_key("x" * 80)

        editor = next(row for row in modal.rows(40) if row.startswith("comment"))

        self.assertIn("│", editor)

    def test_header_sanitizes_quoted_git_path_controls(self) -> None:
        path = '"a/evil\\033]0;owned\\007\\011.py"'
        new_path = path.replace('"a/', '"b/')
        patch = (
            f"diff --git {path} {new_path}\n"
            f"--- {path}\n+++ {new_path}\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )
        modal = open_modal(DiffView.parse(patch, DiffScope.UNSTAGED, True))

        header = modal.rows(100)[0]

        self.assertNotIn("\x1b", header)
        self.assertNotIn("\x07", header)
        self.assertNotIn("\t", header)


class _ApprovalLog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def resolve(self, request_id: str, approved: bool) -> None:
        self.calls.append((request_id, approved))


class _ModalDispatcher:
    def __init__(self) -> None:
        self.diff_interaction = open_modal()
        self.keys: list[str] = []

    async def handle_key(self, app: object, key: str) -> bool:
        _ = app
        self.keys.append(key)
        return True


class InterruptPriorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_approval_cancels_before_underlying_diff(self) -> None:
        interactions = TuiInteractions()
        interactions.diff_interaction = open_modal()
        approvals = _ApprovalLog()
        app = SimpleNamespace(
            interactions=interactions,
            exit_guard=ExitGuard(),
            _pending_approval=SimpleNamespace(request_id="approval-1"),
            approvals=approvals,
            _approval_done=asyncio.Event(),
        )

        await handle_interrupt(app)

        self.assertEqual(approvals.calls, [("approval-1", False)])
        self.assertIsNone(app._pending_approval)
        self.assertTrue(interactions.diff_interaction.active)

    async def test_host_interaction_and_rewind_precede_diff(self) -> None:
        for attribute in ("_pending_interaction", "_rewind_flow"):
            with self.subTest(attribute=attribute):
                interactions = _ModalDispatcher()
                app = SimpleNamespace(
                    interactions=interactions,
                    exit_guard=ExitGuard(),
                    _pending_approval=None,
                    _pending_interaction=None,
                    _rewind_flow=None,
                )
                setattr(app, attribute, object())

                await handle_interrupt(app)

                self.assertEqual(interactions.keys, ["\x1b"])
                self.assertTrue(interactions.diff_interaction.active)


if __name__ == "__main__":
    unittest.main()
