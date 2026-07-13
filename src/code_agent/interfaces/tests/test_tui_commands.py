from __future__ import annotations

import unittest

from code_agent.interfaces.tui_commands import TuiCommandKind, parse_tui_command


class TuiCommandTests(unittest.TestCase):
    def test_parses_chinese_and_english_commands(self) -> None:
        self.assertEqual(parse_tui_command("/任务").command.kind, TuiCommandKind.TASKS)
        self.assertEqual(parse_tui_command("/pause T-042").command.task_id, "T-042")
        self.assertEqual(parse_tui_command("/引导 不要修改公开 API").command.instruction, "不要修改公开 API")
        self.assertFalse(parse_tui_command("/does-not-exist").is_command)
        self.assertEqual(parse_tui_command("/does-not-exist").error, "unknown slash command")
