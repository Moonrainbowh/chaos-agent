import unittest
from collections import deque
from unittest.mock import patch

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import Message
from code_agent.interfaces.attachment_input import AttachmentDraft
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_io import Win32Input, read_key
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.tests.test_attachment_tui import _Ingestor, _image_ref
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class Console:
    def __init__(self, value):
        self.chars = deque(value)

    def kbhit(self):
        return bool(self.chars)

    def getwch(self):
        return self.chars.popleft()


class AltVImageTests(unittest.IsolatedAsyncioTestCase):
    def test_windows_and_escape_key_encodings_consume_only_shortcut(self):
        for sequence in ("\x1bv", "\x1bV", "\x00/", "\xe0/"):
            with self.subTest(sequence=repr(sequence)):
                with patch.dict("sys.modules", {"msvcrt": Console(sequence + "x")}):
                    self.assertEqual(read_key(), "alt+v")
                    self.assertEqual(read_key(), "x")

    def test_native_windows_decoder_does_not_promote_bare_alt_to_image_paste(self):
        class NativeConsole:
            __name__ = "msvcrt"

            def __init__(self):
                self.chars = deque(("\x1b", "v"))

            def kbhit(self):
                return bool(self.chars)

            def getwch(self):
                return self.chars.popleft()

        console = NativeConsole()
        with patch.dict("sys.modules", {"msvcrt": console}):
            with patch("code_agent.interfaces.terminal_io.Win32Input", Win32Input):
                self.assertEqual(read_key(), "\x1b")
                self.assertEqual(read_key(), "v")

    def test_native_windows_decoder_does_not_promote_alt_scan_code_to_image_paste(self):
        class NativeConsole:
            __name__ = "msvcrt"

            def __init__(self):
                self.chars = deque(("\x00", "/"))

            def kbhit(self):
                return bool(self.chars)

            def getwch(self):
                return self.chars.popleft()

        with patch.dict("sys.modules", {"msvcrt": NativeConsole()}):
            with patch("code_agent.interfaces.terminal_io.Win32Input", Win32Input):
                self.assertEqual(read_key(), "")

    def test_native_windows_decoder_ignores_empty_bracketed_paste_frame(self):
        class NativeConsole:
            __name__ = "msvcrt"

            def __init__(self):
                self.chars = deque("\x1b[200~\x1b[201~")

            def kbhit(self):
                return bool(self.chars)

            def getwch(self):
                return self.chars.popleft()

        with patch.dict("sys.modules", {"msvcrt": NativeConsole()}):
            with patch("code_agent.interfaces.terminal_io.Win32Input", Win32Input):
                self.assertEqual(read_key(), "")

    async def test_alt_v_shows_marker_and_sends_image_with_original_text(self):
        event = AgentEvent(EventKind.MESSAGE_ADDED, {"message": Message("user", "accepted").to_dict()})
        engine = FakeEngine((event,))
        draft = AttachmentDraft(_Ingestor())
        output = []
        app = WindowsTerminalApp(AgentController(engine), ApprovalBroker(), attachment_draft=draft, write=output.append)
        value = "\n".join(["explain this image"] * 11)
        app.input.insert_paste(value)
        with patch.dict("sys.modules", {"msvcrt": Console("\x1bv")}):
            await app.handle_key(read_key())
        app.redraw()
        self.assertIn("[image1]", output[-1])
        self.assertIn(f"[chars: {len(value)}]", output[-1])
        self.assertNotIn("screen.png", output[-1])
        self.assertEqual(app.input.text, value)
        self.assertEqual(engine.calls, [])
        await app.handle_key("\r")
        await app.wait_idle()
        self.assertEqual(engine.calls[0][0], value)
        self.assertEqual(engine.attachments, (_image_ref(),))
        app.redraw()
        self.assertNotIn("[image1]", output[-1])

    async def test_empty_clipboard_preserves_text_without_false_marker(self):
        class EmptyIngestor(_Ingestor):
            def ingest_clipboard_items(self, **kwargs):
                return ()

        output = []
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(),
                                 attachment_draft=AttachmentDraft(EmptyIngestor()), write=output.append)
        app.input.insert("draft")
        await app.handle_key("alt+v")
        self.assertIn("clipboard does not contain images", app.state.entries[-1].text)
        app.redraw()
        self.assertNotIn("[image1]", output[-1])
        self.assertEqual(app.input.text, "draft")

    async def test_held_alt_does_not_repeat_empty_clipboard_error(self):
        class EmptyIngestor(_Ingestor):
            def ingest_clipboard_items(self, **kwargs):
                return ()

        app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(),
            attachment_draft=AttachmentDraft(EmptyIngestor()), write=lambda _: None,
        )
        for _ in range(3):
            await app.handle_key("alt+v")
        errors = [entry for entry in app.state.entries if "clipboard does not contain" in entry.text]
        self.assertEqual(len(errors), 1)

    async def test_held_ctrl_v_does_not_repeat_empty_clipboard_error(self):
        class EmptyIngestor(_Ingestor):
            def ingest_clipboard_items(self, **kwargs):
                return ()

        app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(),
            attachment_draft=AttachmentDraft(EmptyIngestor()), write=lambda _: None,
        )
        for _ in range(3):
            await app.handle_key("\x16")
        errors = [entry for entry in app.state.entries if "clipboard does not contain" in entry.text]
        self.assertEqual(len(errors), 1)

    async def test_empty_clipboard_replays_do_not_disarm_ctrl_c_exit_guard(self):
        class EmptyIngestor(_Ingestor):
            def ingest_clipboard_items(self, **kwargs):
                return ()

        app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(),
            attachment_draft=AttachmentDraft(EmptyIngestor()), write=lambda _: None,
        )
        app.running = True
        await app.handle_key("\x03")
        for _ in range(3):
            await app.handle_key("alt+v")
        await app.handle_key("\x03")
        self.assertFalse(app.running)
