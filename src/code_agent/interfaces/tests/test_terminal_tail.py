from __future__ import annotations

import unittest

from code_agent.interfaces.terminal_renderer import ColorMode, render_live_tail
from code_agent.interfaces.terminal_tail import _wrap_plain, render_live_tail_frame
from code_agent.interfaces.i18n import Language
from code_agent.interfaces.terminal_renderer import Theme
from code_agent.interfaces.terminal_status import status_presentation


class TerminalTailPaletteTests(unittest.TestCase):
    def test_live_tail_places_streaming_answer_above_composer(self) -> None:
        frame = render_live_tail_frame(
            "next", "running", 40,
            assistant_draft="first\nsecond",
            terminal_height=12,
            color=ColorMode.NEVER,
        )

        self.assertLess(frame.text.index("◆ 正在回答"), frame.text.index("╭"))
        self.assertIn("first", frame.text)
        self.assertIn("second", frame.text)
        self.assertGreater(frame.geometry.height, 4)

    def test_streaming_answer_is_bounded_and_sanitized(self) -> None:
        draft = "\n".join(f"line {index}" for index in range(30)) + "\x1b[2J"
        frame = render_live_tail_frame(
            "", "running", 32,
            assistant_draft=draft,
            terminal_height=10,
            color=ColorMode.NEVER,
        )

        self.assertIn("…", frame.text)
        self.assertNotIn("\x1b[2J", frame.text)
        self.assertLessEqual(frame.geometry.height, 10)

    def test_streaming_answer_wraps_double_width_text_in_narrow_terminal(self) -> None:
        self.assertEqual(_wrap_plain("中", 1), ["中"])

    def test_picker_highlight_follows_the_selected_marker(self) -> None:
        rendered = render_live_tail(
            "",
            "idle",
            80,
            color=ColorMode.ALWAYS,
            palette=("  first", "› second", "  third"),
        )

        self.assertIn("\x1b[38;5;80m  › second\x1b[0m", rendered)
        self.assertNotIn("\x1b[38;5;80m    first\x1b[0m", rendered)

    def test_non_idle_task_states_never_fall_back_to_ready(self) -> None:
        expected = {
            "building_context": "正在准备工作区",
            "waiting_model": "正在等待模型",
            "verifying": "验证中",
            "paused": "已暂停",
            "waiting_decision": "等待决定",
            "approval": "等待审批",
            "accepted_partial": "部分交付",
        }
        for status, label in expected.items():
            with self.subTest(status=status):
                presentation = status_presentation(
                    status, "", None, Language.ZH_CN, Theme.SYMBOL, 0
                )
                self.assertEqual(presentation[0], label)


if __name__ == "__main__":
    unittest.main()
