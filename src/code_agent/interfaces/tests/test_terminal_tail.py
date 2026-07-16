from __future__ import annotations

import unittest

from code_agent.interfaces.terminal_renderer import ColorMode, render_live_tail
from code_agent.interfaces.i18n import Language
from code_agent.interfaces.terminal_renderer import Theme
from code_agent.interfaces.terminal_status import status_presentation


class TerminalTailPaletteTests(unittest.TestCase):
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
