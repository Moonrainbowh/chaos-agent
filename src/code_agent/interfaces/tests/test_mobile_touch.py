from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_layout import LayoutMode
from code_agent.interfaces.terminal_mouse import MouseClick, mouse_key
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.terminal_style import ColorMode
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.tui_auth_prompt import AuthPrompt
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class MouseDecoderTests(unittest.TestCase):
    def test_both_input_backends_share_press_release_wheel_semantics(self):
        from code_agent.interfaces import terminal_io, posix_terminal_io
        for decoder in (mouse_key, terminal_io._mouse_key, posix_terminal_io._mouse_key):
            self.assertEqual(decoder("\x1b[<0;12;19M"), MouseClick(12, 19))
            self.assertEqual(decoder("\x1b[<0;12;19m"), "")
            self.assertEqual(decoder("\x1b[<64;12;19M"), "scroll_up")
            self.assertEqual(decoder("\x1b[<65;12;19M"), "scroll_down")
            self.assertEqual(decoder("\x1b[<0;0;19M"), "")
            self.assertEqual(decoder("\x1b[<32;12;19M"), "")


class MobileTouchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.size = os.terminal_size((49, 20))
        self.patches = (
            patch("code_agent.interfaces.tui_presentation.shutil.get_terminal_size", return_value=self.size),
            patch("code_agent.interfaces.tui_mobile.terminal_size", return_value=self.size),
        )
        for seam in self.patches:
            seam.start(); self.addCleanup(seam.stop)
        self.output = []
        self.app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=self.output.append)
        self.app.color = ColorMode.NEVER
        self.app.current_thread_id = "thread"
        self.app.redraw()

    async def click(self, action):
        frame = self.app._mobile_frame
        region = next(area for area in frame.touch_regions if area.action == action)
        await self.app.handle_key(MouseClick(region.left + 1, self.size.lines - frame.geometry.height + region.row + 1))

    async def test_commands_single_click_and_cancel_preserve_multiline_draft(self):
        self.app.input.insert("中文草稿\n第二行")
        original = self.app.input
        await self.click("commands")
        self.assertEqual(self.app.input.text, "/")
        self.assertTrue(any(region.action.startswith("picker:") for region in self.app._mobile_frame.touch_regions))
        status = next(region.action for region in self.app._mobile_frame.touch_regions
                      if region.action.startswith("picker:") and region.action.endswith("status"))
        await self.click(status)
        self.assertIs(self.app.input, original)
        self.assertEqual(self.app.input.text, "中文草稿\n第二行")
        await self.click("commands")
        await self.click("commands")
        self.assertIs(self.app.input, original)

    async def test_newline_and_submit_use_keyboard_path_and_do_not_double_submit(self):
        self.app.submit = AsyncMock(return_value=True)
        self.app.input.insert("第一行")
        await self.click("newline")
        self.app.input.insert("第二行")
        await self.click("submit")
        self.app.submit.assert_awaited_once_with("第一行\n第二行")
        self.assertEqual(self.app.input.text, "")
        await self.app.handle_key(MouseClick(49, 1))
        self.assertEqual(self.app.submit.await_count, 1)

    async def test_resize_and_wide_mode_ignore_old_coordinates(self):
        self.app.submit = AsyncMock(return_value=True)
        self.app.input.insert("草稿")
        with patch("code_agent.interfaces.tui_mobile.terminal_size", return_value=os.terminal_size((50, 20))):
            await self.click("submit")
        self.app.submit.assert_not_awaited()
        self.assertEqual(self.app.input.text, "草稿")
        self.app.layout_mode = LayoutMode.WIDE
        await self.click("submit")
        self.app.submit.assert_not_awaited()

    async def test_pause_delegates_to_existing_task_and_keeps_draft(self):
        self.app.input.insert("后续草稿")
        task = SimpleNamespace(done=lambda: False)
        self.app._run_task = task
        self.app._run_started_at = None
        self.app.active_task_id = "active-task"
        self.app.tasks = SimpleNamespace(pause=AsyncMock())
        self.app.redraw()
        await self.click("stop")
        self.app.tasks.pause.assert_awaited_once_with("active-task", "mobile user requested pause")
        self.assertIs(self.app._run_task, task)
        self.assertEqual(self.app.input.text, "后续草稿")

    async def test_running_submit_label_matches_queue_or_steer_mode(self):
        from code_agent.interfaces.tui_submission import SubmitMode
        self.app._run_task = SimpleNamespace(done=lambda: False)
        self.app._run_started_at = None
        for mode, label in ((SubmitMode.QUEUE, "排队"), (SubmitMode.STEER, "引导")):
            self.app.submit_mode = mode
            self.app.redraw()
            self.assertIn("[" + label + "]", self.output[-1])

    async def test_private_auth_touch_is_not_conversation_input(self):
        self.app.input.insert("普通草稿")
        prompt = AuthPrompt("API key", asyncio.get_running_loop().create_future())
        prompt.insert("private-value")
        self.app._auth_prompt = prompt
        self.app.redraw()
        await self.app.handle_key(MouseClick(1, 1))
        self.assertEqual(self.app.input.text, "普通草稿")
        await self.click("submit")
        self.assertEqual(prompt.result.result(), "private-value")
        self.assertEqual(self.app.input.text, "普通草稿")
        self.assertNotIn("private-value", "".join(self.output))

    async def test_bracketed_multiline_paste_keeps_draft_without_submitting(self):
        self.app.submit = AsyncMock(return_value=True)
        await self.app.handle_key("\x1b[200~第一行\n第二行\x1b[201~")
        self.app.submit.assert_not_awaited()
        self.assertEqual(self.app.input.text, "第一行\n第二行")

    async def test_short_picker_keeps_keyboard_selection_visible(self):
        small = os.terminal_size((49, 9))
        with patch("code_agent.interfaces.tui_presentation.shutil.get_terminal_size", return_value=small):
            self.app.input.replace("/")
            self.app.redraw()
            for _ in range(5):
                await self.app.handle_key("down")
            selected = self.app.interactions.picker.selected
            self.assertTrue(any(area.action == "picker:" + selected.identifier
                                for area in self.app._mobile_frame.touch_regions))
