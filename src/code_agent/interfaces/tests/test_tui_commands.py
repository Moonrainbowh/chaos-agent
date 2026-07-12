from __future__ import annotations

import unittest

from code_agent.interfaces.tui_commands import TuiCommandKind, parse_tui_command


class TuiCommandTests(unittest.TestCase):
    def test_parses_chinese_and_english_commands(self) -> None:
        self.assertEqual(parse_tui_command("/任务").kind, TuiCommandKind.TASKS)
        self.assertEqual(parse_tui_command("/pause T-042").task_id, "T-042")
        self.assertEqual(parse_tui_command("/引导 不要修改公开 API").instruction, "不要修改公开 API")
        with self.assertRaises(ValueError): parse_tui_command("/继续")
