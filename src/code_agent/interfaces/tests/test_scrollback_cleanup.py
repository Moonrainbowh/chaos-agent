from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_display import DisplayKind, text_entry
from code_agent.interfaces.terminal_renderer import ColorMode, Theme
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.tui_general_commands import _clear
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class ScrollbackCleanupTests(unittest.TestCase):
    def test_resize_redraw_emits_3j_scrollback_clear(self) -> None:
        writes: list[str] = []
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
            write=writes.append,
        )
        app.theme = Theme.SLATE
        app.color = ColorMode.NEVER
        app.state.entries.append(text_entry(DisplayKind.AGENT, "answer line 1\nanswer line 2"))

        with patch("code_agent.interfaces.tui_presentation.shutil.get_terminal_size") as size:
            size.return_value = os.terminal_size((100, 30))
            app.redraw()
            writes.clear()

            size.return_value = os.terminal_size((80, 30))
            app.redraw()

            self.assertEqual(len(writes), 1)
            self.assertIn("\x1b[3J\x1b[2J\x1b[H", writes[0])
            self.assertIn("answer line 1", writes[0])

    def test_collapse_completed_transcript_emits_3j_scrollback_clear(self) -> None:
        writes: list[str] = []
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
            write=writes.append,
        )
        app.theme = Theme.SLATE
        app.color = ColorMode.NEVER
        app.state.entries.append(text_entry(DisplayKind.TOOL, "tool execution line"))
        app.state.entries.append(text_entry(DisplayKind.AGENT, "final answer line"))

        writes.clear()
        app._collapse_completed_transcript()

        self.assertTrue(any("\x1b[3J\x1b[2J\x1b[H" in write for write in writes))

    def test_clear_command_emits_3j_scrollback_clear(self) -> None:
        writes: list[str] = []
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
            write=writes.append,
        )
        app.theme = Theme.SLATE
        app.state.entries.append(text_entry(DisplayKind.AGENT, "some message"))

        writes.clear()
        success = _clear(app)

        self.assertTrue(success)
        self.assertTrue(any("\x1b[3J\x1b[2J\x1b[H" in write for write in writes))
        self.assertEqual(len(app.state.entries), 0)

    def test_resize_with_large_history_bounds_transcript_to_prevent_scrollback_duplication(self) -> None:
        writes: list[str] = []
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())),
            ApprovalBroker(),
            write=writes.append,
        )
        app.theme = Theme.SLATE
        app.color = ColorMode.NEVER
        for i in range(50):
            app.state.entries.append(text_entry(DisplayKind.AGENT, f"HISTORICAL_LINE_{i}"))

        with patch("code_agent.interfaces.tui_presentation.shutil.get_terminal_size") as size:
            size.return_value = os.terminal_size((100, 30))
            app.redraw()
            writes.clear()

            size.return_value = os.terminal_size((80, 30))
            app.redraw()

            self.assertEqual(len(writes), 1)
            self.assertIn("\x1b[3J\x1b[2J\x1b[H", writes[0])
            # Older history (HISTORICAL_LINE_0) must NOT be re-rendered on resize
            # because doing so would exceed viewport lines and scroll into scrollback.
            self.assertNotIn("HISTORICAL_LINE_0\n", writes[0])
            # Recent history must be present
            self.assertIn("HISTORICAL_LINE_49", writes[0])


if __name__ == "__main__":
    unittest.main()

