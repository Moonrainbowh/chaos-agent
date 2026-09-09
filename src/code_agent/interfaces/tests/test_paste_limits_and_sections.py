import unittest
from collections import deque
from unittest.mock import patch, AsyncMock
from code_agent.interfaces.input_buffer import InputBuffer
from code_agent.interfaces.input_events import MAX_PASTE_BYTES, paste_event
from code_agent.interfaces.terminal_io import read_key
from code_agent.interfaces.terminal_renderer import render_entry, Theme, ColorMode
from code_agent.interfaces.terminal_display import text_entry, DisplayKind
from code_agent.interfaces.terminal_markdown import render_streaming_markdown_rows
from code_agent.interfaces.terminal_tail import render_live_tail_frame
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class Console:
    def __init__(self, text):
        self.text = deque(text)
        self.polls = 0

    def kbhit(self):
        self.polls += 1
        return bool(self.text) and self.polls % 3 == 0

    def getwch(self):
        return self.text.popleft()


class PasteTests(unittest.IsolatedAsyncioTestCase):
    async def test_paste_newlines_stay_in_draft_until_explicit_enter(self):
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        app.input.insert("prefix ")
        with patch.dict("sys.modules", {"msvcrt": Console("\x1b[200~中\r\n文\n\x1b[201~")}):
            await app.handle_key(read_key())
        self.assertEqual(app.input.text, "prefix 中\n文\n")
        self.assertIsNone(app._run_task)
        with patch.object(app, "submit", new_callable=AsyncMock) as submit:
            await app.handle_key("\r")
            submit.assert_awaited_once_with("prefix 中\n文\n")

    async def test_total_limit_rejects_entire_paste_without_changing_cursor(self):
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        app.input.insert("a" * (MAX_PASTE_BYTES - 1))
        app.input.cursor = 7
        await app.handle_key("\x1b[200~中\x1b[201~")
        self.assertEqual(app.input.text, "a" * (MAX_PASTE_BYTES - 1))
        self.assertEqual(app.input.cursor, 7)
        self.assertIsNone(app._run_task)
        self.assertIn("65536", app.state.entries[-1].text)

    def test_unicode_boundary_is_bytes_and_atomic(self):
        buffer = InputBuffer()
        buffer.insert("中" * (MAX_PASTE_BYTES // 3) + "a")
        self.assertEqual(len(buffer.text.encode()), MAX_PASTE_BYTES)
        with self.assertRaises(ValueError):
            buffer.insert("c")
        self.assertEqual(len(buffer.text.encode()), MAX_PASTE_BYTES)
        self.assertEqual(len(paste_event("😀" * (MAX_PASTE_BYTES // 4)).value.encode()), MAX_PASTE_BYTES)

    def test_legacy_multiline_burst_is_paste(self):
        with patch.dict("sys.modules", {"msvcrt": Console("one\r\ntwo\r")}):
            self.assertEqual(read_key(), "\x1b[200~one\r\ntwo\r\x1b[201~")

    def test_legacy_overflow_never_accepts_a_truncated_normalized_prefix(self):
        console = Console("a" + "\r\n" * MAX_PASTE_BYTES)
        console.kbhit = lambda: bool(console.text)
        with patch.dict("sys.modules", {"msvcrt": console}):
            with self.assertRaises(ValueError):
                paste_event(read_key()[6:-6])

    def test_overflow_drains_marker_and_preserves_next_key(self):
        console = Console("\x1b[200~" + "x" * (MAX_PASTE_BYTES + 100) + "\r\x1b[201~" + "\r")
        with patch.dict("sys.modules", {"msvcrt": console}):
            value = read_key()
            with self.assertRaises(ValueError):
                paste_event(value[6:-6])
            self.assertEqual(read_key(), "\r")


class SectionTests(unittest.TestCase):
    def test_headings_use_blank_line_without_automatic_rules(self):
        text = "## One\nbody\n```python\n## code\n```\n## Two\nend"
        result = render_entry(text_entry(DisplayKind.AGENT, text), 60, theme=Theme.SLATE, color=ColorMode.NEVER)
        self.assertIn("─" * 58, result)
        self.assertIn("## code", result)
        rows = render_streaming_markdown_rows(text, 58, ColorMode.NEVER)
        self.assertNotIn("─" * 58, rows)
        self.assertEqual(rows[rows.index("Two") - 1], "")

    def test_background_is_local_to_input_and_disabled_without_color(self):
        frame = render_live_tail_frame("text", "ready", 60, theme=Theme.SLATE, color=ColorMode.ALWAYS)
        self.assertIn("\x1b[48;2;23;48;46m", frame.text)
        plain = render_live_tail_frame("text", "ready", 60, theme=Theme.SLATE, color=ColorMode.NEVER)
        self.assertNotIn("48;2;", plain.text)
