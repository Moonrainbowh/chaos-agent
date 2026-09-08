import contextlib
import io
import unittest
from collections import deque
from unittest.mock import patch

from code_agent.core.errors import ModelStreamError
from code_agent.interfaces.console_shortcuts import _shortcut_prefix
from code_agent.interfaces.terminal_io import _enter_sequence, read_key
from code_agent.interfaces.tests.test_console_shortcuts import event
from code_agent.interfaces.tests import test_image_atom_editing as fixtures
from code_agent.core.cancellation import CancellationToken
from code_agent.providers.errors import ProviderHTTPError


class KeyBoundaryTests(unittest.TestCase):
    def test_native_enter_modifiers_are_captured_from_event(self):
        self.assertEqual(_shortcut_prefix([event(0x10, 16), event(13, 16)]), (2, "shift+enter"))
        self.assertEqual(_shortcut_prefix([event(13)]), (1, "enter"))
        self.assertEqual(_shortcut_prefix([event(13, 8)]), (1, "shift+enter"))

    def test_fast_typing_does_not_swallow_native_enter_or_shift_enter(self):
        for shortcut, expected in [("enter", "\r"), ("shift+enter", "shift+enter")]:
            keys = deque(["x", shortcut])
            class Console:
                def kbhit(self):
                    return bool(keys)
            with patch.dict("sys.modules", {"msvcrt": Console()}), patch(
                "code_agent.interfaces.terminal_io.read_character", side_effect=lambda _: keys.popleft()
            ):
                self.assertEqual(read_key(), "x")
                self.assertEqual(read_key(), expected)

    def test_extended_shift_enter_sequences(self):
        from code_agent.interfaces.tests.test_paste_limits_and_sections import Console
        for sequence in ("\x1b[13;2u", "\x1b[13;2:1u", "\x1b[13;66u", "\x1b[27;2;13~"):
            with patch.dict("sys.modules", {"msvcrt": Console(sequence)}):
                self.assertEqual(read_key(), "shift+enter")

    def test_extended_plain_enter_sequences_are_not_swallowed(self):
        from code_agent.interfaces.tests.test_paste_limits_and_sections import Console
        for sequence in ("\x1b[13u", "\x1b[13;1u", "\x1b[27;1;13~", "\x1b[13;28;13;1;0;1_"):
            with patch.dict("sys.modules", {"msvcrt": Console(sequence)}):
                self.assertEqual(read_key(), "\r")

    def test_windows_vt_shift_enter_sequence_inserts_newline(self):
        from code_agent.interfaces.tests.test_paste_limits_and_sections import Console
        with patch.dict("sys.modules", {"msvcrt": Console("\x1b[13;28;13;1;16;1_")}):
            self.assertEqual(read_key(), "shift+enter")

    def test_windows_vt_enter_key_up_is_ignored(self):
        self.assertEqual(_enter_sequence("\x1b[13;28;13;0;0;1_"), "")

    def test_unrelated_csi_u_key_is_not_misclassified_as_enter(self):
        self.assertEqual(_enter_sequence("\x1b[97;2u"), "")


class ComposerSubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_images_long_paste_newline_and_submit_preserve_payload(self):
        app, draft, engine, output = fixtures.ImageAtomEditingTests().make_app()
        pasted = "一行资料\n" * 12
        await app.handle_key("alt+v")
        await app.handle_key("\x1b[200~" + pasted + "\x1b[201~")
        await app.handle_key("alt+v")
        await app.handle_key("请分析")
        await app.handle_key("shift+enter")
        await app.handle_key("第二行")
        self.assertEqual(engine.calls, [])
        self.assertIn("[image1]", app.input.display[0])
        self.assertIn("[image2]", app.input.display[0])
        self.assertIn("请分析\n第二行", app.input.display[0])
        images = draft.items
        await app.handle_key("\r")
        await app.wait_idle()
        self.assertEqual(engine.calls[0][0], pasted + "请分析\n第二行")
        self.assertEqual(engine.attachments, images)

    async def test_wrapped_502_stays_in_managed_output_and_input_remains_usable(self):
        app, _, _, output = fixtures.ImageAtomEditingTests().make_app()
        async def fail(*args, **kwargs):
            try:
                raise ProviderHTTPError(502, True, "<!DOCTYPE html>" + "page" * 2000)
            except ProviderHTTPError as error:
                raise ModelStreamError("model stream failed") from error
            yield
        app.controller.ask = fail
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            await app._consume("test", CancellationToken())
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("HTTP 502", app.state.entries[-1].text)
        self.assertNotIn("DOCTYPE", "".join(output))
        self.assertNotIn("Traceback", "".join(output))
        await app.handle_key("retry")
        await app.handle_key("shift+enter")
        self.assertEqual(app.input.text, "retry\n")
