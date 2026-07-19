from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path: sys.path.insert(0, str(SRC_ROOT))

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_display import DisplayKind, clip_display, display_width, text_entry
from code_agent.interfaces.terminal_renderer import ColorMode, Theme, render_entries, render_entry, render_live_tail
from code_agent.interfaces.terminal_tail import render_live_tail_frame
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp, render_terminal


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _plain(value: str) -> str:
    return _ANSI.sub("", value)


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
        self.assertIn("38;5;80", agent)
        self.assertIn("38;5;250", tool)

    def test_agent_body_is_white_while_its_marker_keeps_semantic_color(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.AGENT, "answer"), 80, color=ColorMode.ALWAYS)

        self.assertIn("\x1b[38;5;80m◆\x1b[0m", rendered)
        self.assertIn("\x1b[38;5;252manswer\x1b[0m", rendered)

    def test_tool_records_are_dim_while_task_success_is_green(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.TOOL, "read_file completed"), 80, color=ColorMode.ALWAYS)
        completed = render_entry(text_entry(DisplayKind.SUCCESS, "任务已完成"), 80, color=ColorMode.ALWAYS)

        self.assertIn("\x1b[38;5;250m↳\x1b[0m", rendered)
        self.assertIn("\x1b[38;5;250mread_file completed\x1b[0m", rendered)
        self.assertIn("\x1b[38;5;114m✓\x1b[0m", completed)
        self.assertIn("\x1b[38;5;114m任务已完成\x1b[0m", completed)

    def test_cjk_clipping_uses_display_columns(self) -> None:
        self.assertEqual(display_width("ab中文"), 6)
        self.assertEqual(clip_display("ab中文", 5), "ab中")

    def test_live_tail_does_not_clear_screen_or_enable_mouse_tracking(self) -> None:
        tail = render_live_tail("next", "idle", 80, color=ColorMode.NEVER)
        self.assertIn("› next", tail)
        self.assertIn(". idle", tail)
        self.assertNotIn("[2J", tail)
        self.assertNotIn("[?100", tail)

    def test_live_tail_returns_cursor_to_the_end_of_the_input(self) -> None:
        tail = render_live_tail("中文", "idle", 80, color=ColorMode.NEVER)
        self.assertTrue(tail.endswith("\x1b[2A\x1b[8C"))

    def test_empty_composer_has_a_bordered_placeholder_with_cursor_after_prompt(self) -> None:
        tail = render_live_tail("", "idle", 80, color=ColorMode.ALWAYS)
        plain = _plain(tail)
        self.assertIn("│ › 输入任务、编辑请求，或输入 / 查看命令", plain)
        self.assertIn("\x1b[38;5;247m", tail)
        self.assertNotIn("\x1b[2;", tail)
        self.assertIn("╭─────────────────────────────────────────────────────────────────────────────╮", plain)
        self.assertIn("╰─────────────────────────────────────────────────────────────────────────────╯", plain)
        self.assertTrue(tail.endswith("\x1b[2A\x1b[4C"))

    def test_multiline_composer_grows_and_tracks_the_active_row(self) -> None:
        frame = render_live_tail_frame("first\nsecond", "idle", 40, color=ColorMode.NEVER)

        self.assertEqual(frame.geometry.height, 5)
        self.assertEqual(frame.geometry.cursor_row, 2)
        self.assertIn("│ › first", frame.text)
        self.assertIn("│   second", frame.text)

    def test_palette_is_rendered_above_the_composer(self) -> None:
        tail = render_live_tail("/", "idle", 80, color=ColorMode.NEVER, palette=("/帮助", "/状态"))
        self.assertEqual(tail.count("\n"), 5)
        plain = _plain(tail).replace("\r", "")
        self.assertLess(plain.index("/帮助"), plain.index("╭"))
        self.assertLess(plain.index("/状态"), plain.index("╭"))
        self.assertNotIn("idle |", plain)

    def test_palette_moves_the_composer_cursor_below_its_rows(self) -> None:
        frame = render_live_tail_frame("/", "idle", 80, color=ColorMode.NEVER, palette=("/帮助", "/状态"))

        self.assertEqual(frame.geometry.cursor_row, 3)

    def test_transcript_uses_medium_spacing_without_expanding_tool_groups(self) -> None:
        rendered = render_entries(
            (
                text_entry(DisplayKind.USER, "first"),
                text_entry(DisplayKind.TOOL, "read_file completed"),
                text_entry(DisplayKind.AGENT, "done"),
                text_entry(DisplayKind.USER, "next"),
            ),
            80,
            theme=Theme.SYMBOL,
            color=ColorMode.NEVER,
        )

        self.assertIn("› first\n↳ read_file completed\n\n◆ done\n\n› next", rendered)

    def test_status_context_is_right_aligned_and_hidden_when_space_is_tight(self) -> None:
        wide = _plain(render_live_tail("x", "处理中", 80, color=ColorMode.ALWAYS, status_context="gpt-5 · 00:18"))
        narrow = _plain(render_live_tail("x", "处理中", 16, color=ColorMode.NEVER, status_context="gpt-5 · 00:18"))

        self.assertIn("gpt-5 · 00:18", wide)
        self.assertNotIn("gpt-5 · 00:18", narrow)

    def test_compatibility_renderer_is_append_only(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker())
        app.state.entries.append(text_entry(DisplayKind.AGENT, "done"))
        rendered = render_terminal(app.state, "", 80, 24)
        self.assertIn("◆ done", rendered)
        self.assertNotIn("[2J", rendered)

    def test_markdown_heading_and_paragraphs_render_on_separate_lines(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.AGENT, "说明\n\n### 功能说明\n\n- 项目"), 80, color=ColorMode.NEVER)
        self.assertIn("◆ 说明", rendered)
        self.assertIn("  功能说明", rendered)
        self.assertIn("  - 项目", rendered)
        self.assertNotIn("###", rendered)

    def test_markdown_table_uses_three_rules_without_vertical_lines(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.AGENT, "| 位置 | 原来 | 现在 |\n| --- | --- | --- |\n| 函数名 | add(a, b) | multiply(a, b) |"), 44, color=ColorMode.NEVER)

        self.assertIn("─", rendered)
        self.assertIn("函数名", rendered)
        self.assertNotIn("│", rendered)
        self.assertNotIn("| ---", rendered)
        self.assertTrue(all(display_width(line) <= 44 for line in rendered.splitlines()))

    def test_markdown_table_header_and_border_use_distinct_local_colors(self) -> None:
        rendered = render_entry(text_entry(DisplayKind.AGENT, "| A | B |\n| --- | --- |\n| 1 | 2 |"), 40, color=ColorMode.ALWAYS)

        self.assertIn("\x1b[1;96m", rendered)
        self.assertIn("\x1b[38;5;247m", rendered)
