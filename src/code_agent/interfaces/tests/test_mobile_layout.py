from __future__ import annotations

import shutil
import unittest
from unittest.mock import patch

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_layout import LayoutMode
from code_agent.interfaces.terminal_style import ColorMode
from code_agent.interfaces.terminal_tail import render_live_tail_frame
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class MobileLayoutTests(unittest.TestCase):
    def test_actual_terminal_column_boundary_and_overrides(self):
        for width, expected in ((49, LayoutMode.COMPACT), (64, LayoutMode.COMPACT),
                                (65, LayoutMode.WIDE), (100, LayoutMode.WIDE)):
            with self.subTest(width=width):
                self.assertIs(LayoutMode.AUTO.resolve(width), expected)
                self.assertIs(LayoutMode.COMPACT.resolve(width), LayoutMode.COMPACT)
                self.assertIs(LayoutMode.WIDE.resolve(width), LayoutMode.WIDE)

    def test_short_viewports_chinese_and_controls_fit_measured_geometry(self):
        for width in (7, 23, 24, 49, 64, 100):
            for height in (4, 6, 7, 12, 30):
                with self.subTest(width=width, height=height):
                    frame = render_live_tail_frame(
                        "中文输入\n下一行🙂", "运行中", width, terminal_height=height,
                        layout=LayoutMode.COMPACT, color=ColorMode.NEVER,
                        palette=("第一项", "第二项"), status_context="模型 · 42%", active=True,
                    )
                    self.assertLessEqual(frame.geometry.height, height)
                    self.assertTrue(all(value <= width for value in frame.geometry.row_widths))
                    self.assertLess(frame.geometry.cursor_column, width)
                    for target in frame.touch_regions:
                        self.assertLess(target.row, frame.geometry.height)
                        self.assertLessEqual(target.right, width)
                    self.assertNotIn("\x1b[3J", frame.text)

    def test_auto_application_uses_raw_columns_and_docks_controls(self):
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda value: None)
        for width in (64, 65, 49, 100):
            with patch("code_agent.interfaces.tui_presentation.shutil.get_terminal_size",
                       return_value=shutil.os.terminal_size((width, 20))):
                app.redraw()
            self.assertEqual(bool(app._mobile_frame.touch_regions), width <= 64)
            self.assertEqual(app._drawn_size, (width, 20))

    def test_cursor_before_wrapped_character_and_at_full_row_end(self):
        for text, width, index in (("abcdef", 7, 5), ("中文下一字", 8, 3),
                                   ("abcde", 7, 5), ("中文下", 8, 3)):
            with self.subTest(text=text):
                frame = render_live_tail_frame(
                    text, "idle", width, terminal_height=12, cursor_index=index,
                    layout=LayoutMode.COMPACT, color=ColorMode.NEVER,
                )
                self.assertEqual((frame.geometry.cursor_row, frame.geometry.cursor_column), (1, 2))


class MobileLayoutCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_layout_command_preserves_input_and_conversation(self):
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda value: None)
        app.current_thread_id = "thread-1"
        app.input.insert("保留草稿")
        self.assertTrue(await app.submit(":layout compact"))
        self.assertIs(app.layout_mode, LayoutMode.COMPACT)
        self.assertEqual(app.input.text, "保留草稿")
        self.assertEqual(app.current_thread_id, "thread-1")
        self.assertFalse(await app.submit("/layout invalid"))
        self.assertIs(app.layout_mode, LayoutMode.COMPACT)
        self.assertTrue(await app.submit("/layout auto"))
        self.assertIs(app.layout_mode, LayoutMode.AUTO)
