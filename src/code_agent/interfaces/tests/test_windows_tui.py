from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path: sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ModelEvent, ModelEventKind
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.input_buffer import InputBuffer
from code_agent.interfaces.terminal_display import DisplayKind, clip_display, display_width, text_entry
from code_agent.interfaces.terminal_renderer import ColorMode, Theme, render_entry, render_live_tail
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp, render_terminal


class TerminalFirstRendererTests(unittest.TestCase):
    def test_entry_uses_local_ansi_only_and_strips_model_controls(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.ERROR, "bad\x1b[2J\x00"), 80, color=ColorMode.ALWAYS)
        self.assertIn("\x1b[31m", rendered)
        self.assertNotIn("\x1b[2J", rendered)
        self.assertIn("bad?[2J?", rendered)

    def test_never_color_has_ascii_fallback(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.SUCCESS, "saved"), 80, theme=Theme.SIGNAL, color=ColorMode.NEVER)
        self.assertEqual(rendered, "+ saved")
        self.assertNotIn("\x1b", rendered)

    def test_user_agent_and_tool_rows_have_distinct_semantic_colors(self) -> None:
        user = render_entry(text_entry(DisplayKind.USER, "request"), 80, color=ColorMode.ALWAYS)
        agent = render_entry(text_entry(DisplayKind.AGENT, "answer"), 80, color=ColorMode.ALWAYS)
        tool = render_entry(text_entry(DisplayKind.TOOL, "read_file"), 80, color=ColorMode.ALWAYS)

        self.assertIn("38;5;80", user)
        self.assertIn("38;5;121", agent)
        self.assertIn("38;5;153", tool)

    def test_cjk_clipping_uses_display_columns(self) -> None:
        self.assertEqual(display_width("ab中文"), 6)
        self.assertEqual(clip_display("ab中文", 5), "ab中")

    def test_live_tail_does_not_clear_screen_or_enable_mouse_tracking(self) -> None:
        tail = render_live_tail("next", "idle", 80, color=ColorMode.NEVER)
        self.assertIn("> next", tail)
        self.assertIn(". idle", tail)
        self.assertNotIn("[2J", tail)
        self.assertNotIn("[?100", tail)

    def test_live_tail_returns_cursor_to_the_end_of_the_input(self) -> None:
        tail = render_live_tail("中文", "idle", 80, color=ColorMode.NEVER)
        self.assertTrue(tail.endswith("\x1b[7C"))

    def test_empty_composer_has_a_bordered_placeholder_with_cursor_after_prompt(self) -> None:
        tail = render_live_tail("", "idle", 80, color=ColorMode.NEVER)
        self.assertIn("[> 输入任务、编辑请求，或输入 / 查看命令", tail)
        self.assertTrue(tail.endswith("\x1b[3C"))

    def test_palette_is_rendered_in_the_status_line_not_as_scrollback_rows(self) -> None:
        tail = render_live_tail("/", "idle", 80, color=ColorMode.NEVER, palette=("/帮助", "/状态"))
        self.assertEqual(tail.count("\n"), 1)
        self.assertIn("/帮助  /状态", tail)

    def test_compatibility_renderer_is_append_only(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker())
        app.state.entries.append(text_entry(DisplayKind.AGENT, "done"))
        rendered = render_terminal(app.state, "", 80, 24)
        self.assertIn("* done", rendered)
        self.assertNotIn("[2J", rendered)

    def test_markdown_heading_and_paragraphs_render_on_separate_lines(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.AGENT, "说明\n\n### 功能说明\n\n- 项目"), 80, color=ColorMode.NEVER)
        self.assertIn("* 说明", rendered)
        self.assertIn("  功能说明", rendered)
        self.assertIn("  - 项目", rendered)
        self.assertNotIn("###", rendered)

    def test_markdown_table_has_borders_headers_and_column_widths(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.AGENT, "| 位置 | 原来 | 现在 |\n| --- | --- | --- |\n| 函数名 | add(a, b) | multiply(a, b) |"), 44, color=ColorMode.NEVER)

        self.assertIn("+", rendered)
        self.assertIn("函数名", rendered)
        self.assertNotIn("| ---", rendered)
        self.assertTrue(all(display_width(line) <= 44 for line in rendered.splitlines()))

    def test_markdown_table_header_and_border_use_distinct_local_colors(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.AGENT, "| A | B |\n| --- | --- |\n| 1 | 2 |"), 40, color=ColorMode.ALWAYS)

        self.assertIn("\x1b[1;96m", rendered)
        self.assertIn("\x1b[2m", rendered)


class InputBufferTests(unittest.TestCase):
    def test_editing_history_and_clear_shortcut_state(self) -> None:
        buffer = InputBuffer()
        buffer.insert("abc"); buffer.move_left(); buffer.insert("X")
        self.assertEqual(buffer.text, "abXc")
        self.assertEqual(buffer.submit(), "abXc")
        buffer.previous(); self.assertEqual(buffer.text, "abXc")
        buffer.clear(); self.assertEqual(buffer.text, "")


class WindowsTerminalAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_appends_user_and_completed_agent_entries(self) -> None:
        events = (
            AgentEvent(EventKind.RUN_STARTED, {"thread_id": "thread-1"}),
            AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="hello").to_dict()}),
            AgentEvent(EventKind.COMPLETED, {"thread_id": "thread-1"}),
        )
        output: list[str] = []
        app = WindowsTerminalApp(AgentController(FakeEngine(events)), ApprovalBroker(), write=output.append)
        self.assertTrue(await app.submit("inspect")); await app.wait_idle()
        self.assertEqual([entry.kind for entry in app.state.entries], [DisplayKind.USER, DisplayKind.AGENT])
        self.assertEqual(app.state.status, "completed")
        self.assertNotIn("[2J", "".join(output))
        await asyncio.sleep(0)

    async def test_streamed_answer_is_written_once_after_completion(self) -> None:
        events = (
            AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="full ").to_dict()}),
            AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="answer").to_dict()}),
            AgentEvent(EventKind.COMPLETED, {}),
        )
        output: list[str] = []
        app = WindowsTerminalApp(AgentController(FakeEngine(events)), ApprovalBroker(), write=output.append)
        await app.submit("inspect"); await app.wait_idle()
        rendered = "".join(output)
        self.assertEqual(rendered.count("* full answer"), 1)
        self.assertNotIn("* full \n", rendered)

    async def test_raw_reasoning_is_not_written(self) -> None:
        events = (
            AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.REASONING_DELTA, text="private work").to_dict()}),
            AgentEvent(EventKind.COMPLETED, {}),
        )
        app = WindowsTerminalApp(AgentController(FakeEngine(events)), ApprovalBroker(), write=lambda _: None)
        await app.submit("inspect"); await app.wait_idle()
        self.assertNotIn("private work", "\n".join(entry.text for entry in app.state.entries))

    async def test_new_prompt_clears_the_old_status_before_appending_it(self) -> None:
        output: list[str] = []
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=output.append)
        app.state.status = "completed"; app.state.execution_summary = "已完成 3 项操作"
        app.redraw()

        await app.submit("修改成乘法函数吧")

        appended_prompt = output[1]
        self.assertIn("> 修改成乘法函数吧", appended_prompt)
        self.assertNotIn("已完成 3 项操作", appended_prompt)
        self.assertNotIn("\x1b[1B", appended_prompt)
        await app.wait_idle()

    async def test_running_icon_changes_but_completion_icon_is_static(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        app.state.begin_run(); first = app._status_presentation()
        app._spinner_index = 1; second = app._status_presentation()
        app.state.status = "completed"; app.state.execution_summary = "已完成 1 项操作"

        self.assertNotEqual(first[1], second[1])
        self.assertEqual(app._status_presentation()[1], "+")

    async def test_arrow_keys_and_ctrl_u_edit_instead_of_printing_escape_bytes(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        for key in ("a", "b", "left", "X"):
            await app.handle_key(key)
        self.assertEqual(app.input.text, "aXb")
        await app.handle_key("\x15")
        self.assertEqual(app.input.text, "")

    async def test_malformed_slash_command_is_in_band_error(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker(), write=lambda _: None)
        self.assertFalse(await app.submit("/does-not-exist"))
        self.assertEqual(app.state.entries[-1].kind, DisplayKind.ERROR)


if __name__ == "__main__": unittest.main()
