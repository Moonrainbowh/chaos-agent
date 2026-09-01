from __future__ import annotations

import asyncio
import re
import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path: sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ModelEvent, ModelEventKind
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.input_buffer import InputBuffer
from code_agent.interfaces.input_events import ExitGuard, MAX_PASTE_BYTES, paste_event
from code_agent.interfaces.terminal_display import DisplayKind, clip_display, display_width, text_entry
from code_agent.interfaces.terminal_renderer import ColorMode, Theme, render_entries, render_entry, render_live_tail
from code_agent.interfaces.terminal_tail import render_live_tail_frame
from code_agent.interfaces.terminal_status import status_context, status_presentation
from code_agent.interfaces.terminal_io import BRACKETED_PASTE_DISABLE, BRACKETED_PASTE_ENABLE
from code_agent.interfaces.terminal_state import ApprovalBroker, ApprovalRequest
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp, render_terminal


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _plain(value: str) -> str:
    return _ANSI.sub("", value)


class InputBufferTests(unittest.TestCase):
    def test_editing_history_and_clear_shortcut_state(self) -> None:
        buffer = InputBuffer()
        buffer.insert("abc"); buffer.move_left(); buffer.insert("X")
        self.assertEqual(buffer.text, "abXc")
        self.assertEqual(buffer.submit(), "abXc")
        buffer.previous(); self.assertEqual(buffer.text, "abXc")
        buffer.clear(); self.assertEqual(buffer.text, "")

    def test_line_break_and_vertical_cursor_movement(self) -> None:
        buffer = InputBuffer()
        buffer.insert("one"); buffer.insert_line_break(); buffer.insert("xy")

        self.assertEqual(buffer.text, "one\nxy")
        self.assertTrue(buffer.move_up()); self.assertEqual(buffer.cursor, 2)
        self.assertTrue(buffer.move_down()); self.assertEqual(buffer.cursor, 6)
        buffer.move_home(); self.assertEqual(buffer.cursor, 4)
        buffer.move_end(); self.assertEqual(buffer.cursor, 6)

    def test_paste_normalizes_newlines_and_is_bounded(self) -> None:
        self.assertEqual(paste_event("第一行\r\n第二行\r第三行").value, "第一行\n第二行\n第三行")
        with self.assertRaises(ValueError):
            paste_event("x" * (MAX_PASTE_BYTES + 1))

    def test_exit_guard_requires_second_interrupt_and_disarms_on_input(self) -> None:
        clock = [0.0]
        guard = ExitGuard(clock=lambda: clock[0])
        self.assertFalse(guard.interrupt())
        clock[0] = 1.9; self.assertTrue(guard.interrupt())
        self.assertFalse(guard.interrupt())
        guard.input_received(); clock[0] = 2.0
        self.assertFalse(guard.interrupt())

    def test_bracketed_paste_terminal_modes_are_explicit(self) -> None:
        self.assertEqual(BRACKETED_PASTE_ENABLE, "\x1b[?2004h")
        self.assertEqual(BRACKETED_PASTE_DISABLE, "\x1b[?2004l")

if __name__ == "__main__": unittest.main()
