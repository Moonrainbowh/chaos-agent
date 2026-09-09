import os
import unittest
from unittest.mock import patch

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.terminal_tail import render_live_tail_frame
from code_agent.interfaces.terminal_tail_geometry import tail_geometry, resized_tail_geometry
from code_agent.interfaces.terminal_renderer import ColorMode, Theme
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class ResizeReflowTests(unittest.TestCase):
    def test_wide_rows_and_cursor_are_remapped_after_shrinking(self):
        old = tail_geometry(["x" * 118] * 9, 3, 84)
        new = resized_tail_geometry(old, 40)
        self.assertEqual(new.height, 27)
        self.assertEqual(new.cursor_row, 11)

    def test_color_sequences_do_not_inflate_physical_row_count(self):
        old = tail_geometry(["\x1b[31m中文\x1b[0m", "x" * 80], 1, 4)
        self.assertEqual(old.row_widths, (4, 80))
        new = resized_tail_geometry(old, 40)
        self.assertEqual((new.height, new.cursor_row), (3, 1))

    def test_expand_then_shrink_erases_all_reflowed_tail_rows_in_one_write(self):
        writes = []
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=writes.append)
        app.theme = Theme.SLATE
        app.color = ColorMode.NEVER
        app.input.insert("draft 中文")
        with patch("code_agent.interfaces.tui_presentation.shutil.get_terminal_size") as size:
            for columns in (80, 160, 60, 140, 80):
                old = app._tail_geometry
                expected = resized_tail_geometry(old, columns) if old else None
                size.return_value = os.terminal_size((columns, 60))
                writes.clear()
                app.redraw()
                self.assertEqual(len(writes), 1)
                self.assertEqual(writes[0].count("CHAOS AGENT"), 1)
                if expected:
                    self.assertEqual(writes[0].count("\x1b[2K"), max(expected.height, app._tail_geometry.height))
                    self.assertIn(f"\x1b[{expected.cursor_row}A", writes[0])
                self.assertEqual(app.input.text, "draft 中文")

    def test_current_short_viewport_bounds_cleanup(self):
        old = render_live_tail_frame("draft", "ready", 160, theme=Theme.SLATE).geometry
        frame = render_live_tail_frame("draft", "ready", 40, terminal_height=8,
                                       previous=resized_tail_geometry(old, 40), theme=Theme.SLATE)
        self.assertEqual(frame.text.count("\x1b[2K"), 8)

    def test_append_before_redraw_uses_resized_geometry(self):
        from code_agent.interfaces.terminal_display import DisplayKind
        writes = []
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=writes.append)
        app.theme = Theme.SLATE
        with patch("code_agent.interfaces.tui_presentation.shutil.get_terminal_size") as size:
            size.return_value = os.terminal_size((160, 60))
            app.redraw()
            expected = resized_tail_geometry(app._tail_geometry, 60)
            size.return_value = os.terminal_size((60, 60))
            writes.clear()
            app._append(DisplayKind.METADATA, "clipboard images staged")
            self.assertEqual(writes[0].count("\x1b[2K"), expected.height)
            self.assertIsNone(app._tail_geometry)
