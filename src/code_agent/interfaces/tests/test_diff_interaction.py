from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from code_agent.interfaces._diff_parser import DiffScope
from code_agent.interfaces.diff_feedback import (
    MAX_DIFF_FEEDBACK_BYTES,
    MAX_DIFF_FEEDBACK_CHARS,
)
from code_agent.interfaces.diff_interaction import (
    DiffAction,
    DiffInteraction,
    DiffMode,
)
from code_agent.interfaces.diff_view import DiffComment, DiffSourceDocument, DiffView
from code_agent.interfaces.input_buffer import InputBuffer
from code_agent.interfaces.input_events import ExitGuard, MAX_PASTE_BYTES
from code_agent.interfaces.terminal_io import read_key
from code_agent.interfaces.tui_input import handle_interrupt
from code_agent.interfaces.tui_interactions import TuiInteractions


def unified(path: str, *, second_hunk: bool = True) -> str:
    later = (
        "@@ -10,2 +10,2 @@\n-old10\n+new10\n tail\n"
        if second_hunk
        else ""
    )
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n+++ b/{path}\n"
        "@@ -1,2 +1,2 @@\n-old1\n+new1\n same\n"
        f"{later}"
    )


def make_view() -> DiffView:
    return DiffView.from_documents(
        (
            DiffSourceDocument(
                DiffScope.UNSTAGED, unified("src/first.py"), True
            ),
            DiffSourceDocument(
                DiffScope.STAGED, unified("tests/second.py"), True
            ),
        )
    )


class MutableSource:
    def __init__(self) -> None:
        self.fail = False
        self.calls = 0

    async def read_diff(self, paths: tuple[str, ...] = ()) -> object:
        self.calls += 1
        if self.fail:
            raise OSError("sensitive source detail")
        return (
            DiffSourceDocument(
                DiffScope.UNSTAGED, unified("src/live.py"), True
            ),
        )


class AppStub:
    def __init__(self) -> None:
        self.state = SimpleNamespace(diff="")
        self.input = InputBuffer()
        self._pending_approval = None
        self.submitted: list[str] = []
        self.accept_submit = True

    def _columns(self) -> int:
        return 100

    async def submit(self, text: str, **_: object) -> bool:
        self.submitted.append(text)
        return self.accept_submit


class DiffInteractionStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.modal = DiffInteraction()
        self.modal.open(
            make_view(),
            scope=DiffScope.WORKING_TREE,
            recorded_diff=None,
        )

    def test_parser_supplies_stable_old_new_and_hunk_anchor(self) -> None:
        view = self.modal.view
        assert view is not None

        comment = view.comment(2, "new line note")

        self.assertIsNone(comment.old_line)
        self.assertEqual(comment.new_line, 1)
        self.assertEqual(comment.hunk, "@@ -1,2 +1,2 @@")
        feedback = self.modal.feedback()
        self.assertIn('"scope":"unstaged"', feedback)
        self.assertIn('"path":"src/first.py"', feedback)
        self.assertIn('"new":1', feedback)

    def test_direct_comment_rejects_non_text_without_attribute_errors(self) -> None:
        with self.assertRaises(TypeError):
            DiffComment(7, 0, "note")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            DiffComment("file.py", 0, object())  # type: ignore[arg-type]

    def test_file_line_hunk_page_and_filter_navigation(self) -> None:
        self.assertIs(self.modal.handle_key("page_down"), DiffAction.NONE)
        self.assertEqual(self.modal.line_index, 6)
        self.modal.handle_key("[")
        self.assertEqual(self.modal.line_index, 4)
        self.modal.handle_key("right")
        self.assertEqual(self.modal.line_index, 0)
        self.assertEqual(self.modal.view.current.path, "tests/second.py")  # type: ignore[union-attr]

        self.modal.handle_key("/")
        self.assertEqual(self.modal.mode, DiffMode.FILTER)
        self.modal.editor.clear()
        action = self.modal.handle_key("\x1b[200~first\r\n\x1b[201~")
        self.assertIs(action, DiffAction.NONE)
        self.assertEqual(self.modal.mode, DiffMode.FILTER)
        self.modal.handle_key("\r")

        self.assertEqual(self.modal.mode, DiffMode.BROWSE)
        self.assertEqual(self.modal.view.current.path, "src/first.py")  # type: ignore[union-attr]

    def test_escape_cancels_editor_but_saved_comments_require_discard(self) -> None:
        self.modal.handle_key("c")
        self.modal.handle_key("draft")
        self.modal.handle_key("\x1b")

        self.assertEqual(self.modal.mode, DiffMode.BROWSE)
        self.assertEqual(self.modal.view.comments, ())  # type: ignore[union-attr]
        self.assertTrue(self.modal.active)

        for key in ("c", "saved", "\r", "\x1b"):
            self.modal.handle_key(key)
        self.assertEqual(self.modal.mode, DiffMode.DISCARD)
        self.modal.handle_key("\r")
        self.assertEqual(self.modal.mode, DiffMode.BROWSE)
        self.modal.handle_key("\x1b")
        self.modal.handle_key("y")
        self.assertFalse(self.modal.active)

    def test_comment_that_would_exceed_feedback_limit_is_rolled_back(self) -> None:
        large_paste = "\x1b[200~" + "x" * 400 + "\x1b[201~"
        for key in ("c", large_paste, "\r"):
            self.modal.handle_key(key)
        self.assertEqual(len(self.modal.view.comments), 1)  # type: ignore[union-attr]
        self.modal.handle_key("down")
        for key in ("c", large_paste, "\r"):
            self.modal.handle_key(key)

        self.assertEqual(len(self.modal.view.comments), 1)  # type: ignore[union-attr]
        self.assertIn("too large", self.modal.error or "")
        self.assertLessEqual(
            len(self.modal.feedback().encode("utf-8")), MAX_DIFF_FEEDBACK_BYTES
        )
        self.assertLessEqual(len(self.modal.feedback()), MAX_DIFF_FEEDBACK_CHARS)

    def test_oversized_editor_paste_is_an_in_band_error(self) -> None:
        self.modal.handle_key("c")
        paste = "\x1b[200~" + "x" * (MAX_PASTE_BYTES + 1) + "\x1b[201~"

        action = self.modal.handle_key(paste)

        self.assertIs(action, DiffAction.NONE)
        self.assertEqual(self.modal.mode, DiffMode.COMMENT)
        self.assertIn("256 KiB", self.modal.error or "")


class DiffTuiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_modal_consumes_keys_refresh_keeps_snapshot_and_send_calls_once(self) -> None:
        source = MutableSource()
        controls = TuiInteractions(source)
        app = AppStub()
        await controls.show_diff(app)
        old_view = controls.diff_interaction.view

        self.assertTrue(await controls.handle_key(app, "unhandled printable"))
        self.assertEqual(app.input.text, "")
        source.fail = True
        self.assertTrue(await controls.handle_key(app, "r"))
        self.assertIs(controls.diff_interaction.view, old_view)
        self.assertIn("refresh failed", "\n".join(controls.rows(app)))

        for key in ("c", "fix this", "\r", "s"):
            self.assertTrue(await controls.handle_key(app, key))

        self.assertEqual(len(app.submitted), 1)
        self.assertIn('"path":"src/live.py"', app.submitted[0])
        self.assertFalse(controls.diff_interaction.active)

    async def test_failed_initial_load_is_an_in_band_retryable_modal(self) -> None:
        source = MutableSource()
        source.fail = True
        controls = TuiInteractions(source)
        app = AppStub()

        await controls.show_diff(app)

        self.assertTrue(controls.diff_interaction.active)
        rows = controls.rows(app)
        self.assertTrue(any("load failed" in row for row in rows))
        self.assertTrue(any("r refresh" in row for row in rows))

    async def test_submit_exception_is_in_band_and_keeps_comments(self) -> None:
        controls = TuiInteractions(MutableSource())
        app = AppStub()
        await controls.show_diff(app)
        for key in ("c", "keep this", "\r"):
            await controls.handle_key(app, key)

        async def fail(_: str) -> bool:
            raise RuntimeError("task boundary rejected feedback")

        app.submit = fail
        self.assertTrue(await controls.handle_key(app, "s"))

        self.assertTrue(controls.diff_interaction.active)
        self.assertTrue(controls.diff_interaction.has_unsent_comments)
        self.assertIn("submit failed", "\n".join(controls.rows(app)))

    async def test_ctrl_c_closes_diff_before_arming_process_exit(self) -> None:
        controls = TuiInteractions()
        controls.diff_interaction.open(
            make_view(), scope=DiffScope.WORKING_TREE, recorded_diff=None
        )
        app = SimpleNamespace(
            interactions=controls,
            exit_guard=ExitGuard(),
        )

        await handle_interrupt(app)

        self.assertFalse(controls.diff_interaction.active)
        self.assertFalse(app.exit_guard.interrupt())


class TerminalPageKeyTests(unittest.TestCase):
    def test_windows_extended_page_keys_are_normalized(self) -> None:
        class FakeMsvcrt:
            def __init__(self, values: list[str]) -> None:
                self.values = values

            def getwch(self) -> str:
                return self.values.pop(0)

            def kbhit(self) -> bool:
                return bool(self.values)

        for suffix, expected in (("I", "page_up"), ("Q", "page_down")):
            with self.subTest(suffix=suffix):
                fake = FakeMsvcrt(["\xe0", suffix])
                with patch.dict(sys.modules, {"msvcrt": fake}):
                    self.assertEqual(read_key(), expected)


if __name__ == "__main__":
    unittest.main()
