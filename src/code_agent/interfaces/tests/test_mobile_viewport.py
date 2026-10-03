from __future__ import annotations

import os
import re
import unittest
from unittest.mock import AsyncMock, patch

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_display import DisplayKind, display_width, text_entry
from code_agent.interfaces.terminal_layout import LayoutMode
from code_agent.interfaces.terminal_mouse import MouseClick
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.terminal_style import ColorMode
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class MobileViewportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.output = []
        self.app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(),
                                     project_name="中文工程", write=self.output.append)
        self.app.layout_mode = LayoutMode.COMPACT
        self.app.color = ColorMode.NEVER
        self.app.current_thread_id = "actual-thread"
        self.size = os.terminal_size((49, 30))
        self.patches = [patch("code_agent.interfaces.tui_presentation.shutil.get_terminal_size", return_value=self.size),
                        patch("code_agent.interfaces.tui_mobile.terminal_size", return_value=self.size)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_short_history_and_live_markdown_start_below_header(self):
        self.app.state.entries = [text_entry(DisplayKind.USER, "第一句")]
        self.app.state._draft.append("## 实际标题\n正文 **强调**")
        self.app.redraw()
        rows = self.output[-1].split("\n\r")
        self.assertIn("中文工程", rows[0])
        self.assertIn("actual-thread", rows[1])
        self.assertIn("第一句", rows[2])
        self.assertIn("实际标题", self.output[-1])
        self.assertNotIn("## 实际标题", self.output[-1])
        self.assertNotIn("**强调**", self.output[-1])
        self.assertEqual(len(self.app.state.entries), 1)

    def test_append_and_scroll_keep_header_fixed_and_latest_selects_tail(self):
        self.app.state.entries = [text_entry(DisplayKind.USER, f"消息{i}") for i in range(50)]
        self.app.redraw()
        self.assertIn("消息49", self.output[-1])
        self.app.scroll_to_history_start()
        self.app.redraw()
        self.assertIn("消息0", self.output[-1])
        self.assertNotIn("消息49", self.output[-1])
        self.assertIn("中文工程", self.output[-1].split("\n\r")[0])
        self.app.scroll_to_bottom()
        self.app._append(DisplayKind.USER, "新尾部")
        self.assertIn("新尾部", self.output[-1])
        self.assertTrue(self.output[-1].startswith("\x1b[?25l\x1b[2J\x1b[H"))

    def test_green_surface_and_no_color_have_same_chinese_content(self):
        self.app.state.entries = [text_entry(DisplayKind.USER, "中文宽度" * 12)]
        self.app.color = ColorMode.ALWAYS
        self.app.redraw()
        self.assertIn("48;2;23;48;45", self.output[-1])
        self.assertNotIn("48;2;30;48;76", self.output[-1])
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.app.color = ColorMode.AUTO
            self.app.redraw()
        self.assertNotRegex(self.output[-1], r"\x1b\[[0-9;]*m")
        for row in self.output[-1].split("\n\r"):
            clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", row)
            self.assertLessEqual(display_width(clean), 49)

    async def test_header_sessions_opens_picker_not_resume_and_modal_ignores_header(self):
        self.app.submit = AsyncMock(return_value=True)
        self.app.redraw()
        await self.app.handle_key(MouseClick(48, 2))
        self.app.submit.assert_awaited_once_with("/sessions history")
        self.app._pending_approval = object()
        await self.app.handle_key(MouseClick(48, 2))
        self.assertEqual(self.app.submit.await_count, 1)

    def test_collapsed_four_large_buttons_and_long_model_preserve_real_metrics(self):
        from code_agent.interfaces.terminal_tail import render_live_tail_frame
        self.app.composer_expanded = False
        frame = render_live_tail_frame("", "运行中", 49, terminal_height=25,
                    layout=LayoutMode.COMPACT, color=ColorMode.NEVER, expanded=False,
                    status_context="very-long-provider/model-name-that-keeps-going · 1200 tokens (20%) · 43.0 token/s")
        self.assertIn("1200 tokens (20%)", frame.text)
        self.assertIn("43.0 token/s", frame.text)
        actions = {region.action for region in frame.touch_regions}
        self.assertEqual(actions, {"compose", "commands", "latest", "status"})

    def test_wide_keeps_append_transcript_without_mobile_header(self):
        self.app.layout_mode = LayoutMode.WIDE
        self.app._append(DisplayKind.USER, "wide message")
        self.assertIn("wide message", self.output[-1])
        self.assertNotIn("[项目]", self.output[-1])
        self.assertNotIn("\x1b[2J", self.output[-1])

    def test_status_redraw_reuses_history_and_append_formats_only_new_entry(self):
        from code_agent.interfaces import terminal_mobile_viewport as view
        self.app.state.entries = [text_entry(DisplayKind.USER, f"问题{i}") for i in range(25)]
        with patch.object(view, "mobile_history", wraps=view.mobile_history) as render:
            self.app.redraw()
            self.app.redraw()
            self.assertEqual(render.call_count, 1)
            self.app.state.entries.append(text_entry(DisplayKind.AGENT, "追加答案"))
            self.app.redraw()
            self.assertEqual(len(render.call_args.args[0]), 1)

    def test_streaming_redraw_does_not_pollute_durable_cache_and_clear_removes_draft(self):
        self.app.state.entries = [text_entry(DisplayKind.USER, "持久问题")]
        self.app.state._draft.append("临时回答唯一文本")
        self.app.redraw()
        cached = tuple(self.app._compact_history_lines)
        self.app.redraw()
        self.assertEqual(self.output[-1].count("临时回答唯一文本"), 1)
        self.assertEqual(tuple(self.app._compact_history_lines), cached)
        self.assertFalse(any("临时回答唯一文本" in row for row in cached))
        self.app.state._draft.clear()
        self.app.redraw()
        self.assertNotIn("临时回答唯一文本", self.output[-1])
        self.assertNotIn("正在回复", self.output[-1])
        self.assertEqual(tuple(self.app._compact_history_lines), cached)
