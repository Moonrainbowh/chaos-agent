from __future__ import annotations

import unittest

from code_agent.interfaces.command_registry import REGISTRY
from code_agent.interfaces.tui_commands import TuiCommandKind, parse_tui_command


class TuiCommandTests(unittest.TestCase):
    def test_parses_chinese_and_english_commands(self) -> None:
        self.assertEqual(parse_tui_command("/任务").command.kind, TuiCommandKind.TASKS)
        self.assertEqual(parse_tui_command("/evidence").command.kind, TuiCommandKind.EVIDENCE)
        self.assertEqual(parse_tui_command("/accept T-042").command.kind, TuiCommandKind.ACCEPT)
        self.assertEqual(parse_tui_command("/接受 T-042").command.task_id, "T-042")
        self.assertEqual(parse_tui_command("/pause T-042").command.task_id, "T-042")
        self.assertEqual(parse_tui_command("/引导 不要修改公开 API").command.instruction, "不要修改公开 API")
        self.assertFalse(parse_tui_command("/does-not-exist").is_command)
        self.assertEqual(parse_tui_command("/does-not-exist").error, "unknown or unavailable slash command")

    def test_registry_drives_parse_and_availability(self) -> None:
        visible = REGISTRY.filter("/模", {"profiles"})
        spec, arguments, error = REGISTRY.parse('/模型 使用 "local test"', {"profiles"})

        self.assertEqual(visible[0].name, "模型")
        self.assertEqual(spec.name, "模型")
        self.assertEqual(arguments, ("使用", "local test"))
        self.assertIsNone(error)
        self.assertEqual(REGISTRY.parse("/mcp", set())[2], "unknown or unavailable slash command")

    def test_compound_commands_declare_their_secondary_actions(self) -> None:
        model = REGISTRY.resolve("模型")
        mcp = REGISTRY.resolve("mcp")

        self.assertEqual(tuple(action.name for action in model.actions), ("列表", "使用"))
        self.assertEqual(
            tuple(action.name for action in mcp.actions),
            ("列表", "状态", "启用", "禁用", "重启", "诊断"),
        )
