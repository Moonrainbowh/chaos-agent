from __future__ import annotations

import unittest

from code_agent.interfaces.command_availability import available_services
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

    def test_rewind_registry_is_read_only_and_has_exact_actions(self) -> None:
        rewind = REGISTRY.resolve("rewind")
        self.assertEqual(rewind.name, "回溯")
        self.assertEqual(rewind.aliases, ("rewind",))
        self.assertEqual(rewind.group, "工作区")
        self.assertEqual(rewind.description, "只读预览 checkpoint 回溯")
        self.assertEqual(rewind.usage, "<action>")
        self.assertEqual(rewind.requires, ("rewind",))
        self.assertEqual(
            tuple((item.name, item.aliases, item.description, item.usage) for item in rewind.actions),
            (
                ("列表", ("list",), "列出 checkpoint 候选", "[cursor]"),
                (
                    "预览",
                    ("preview",),
                    "预览 checkpoint 回溯",
                    "<checkpoint-id> <conversation|code|both>",
                ),
            ),
        )

    def test_rewind_preserves_raw_instruction_and_malformed_quotes(self) -> None:
        parsed = parse_tui_command(
            '/rewind preview "checkpoint one" both', {"rewind"}
        )
        malformed = parse_tui_command(
            '/rewind preview "unterminated', {"rewind"}
        )
        chinese = parse_tui_command('/回溯 列表 "opaque cursor"', {"rewind"})
        self.assertEqual(parsed.command.kind, TuiCommandKind.REWIND)
        self.assertEqual(
            parsed.command.instruction, 'preview "checkpoint one" both'
        )
        self.assertEqual(
            malformed.command.instruction, 'preview "unterminated'
        )
        self.assertEqual(chinese.command.instruction, '列表 "opaque cursor"')

    def test_default_and_explicit_empty_services_do_not_enable_rewind(self) -> None:
        self.assertEqual(
            parse_tui_command("/rewind list").error,
            "unknown or unavailable slash command",
        )
        self.assertEqual(
            parse_tui_command("/任务", set()).error,
            "unknown or unavailable slash command",
        )
        self.assertEqual(
            parse_tui_command("/rewind list", set()).error,
            "unknown or unavailable slash command",
        )

    def test_available_services_only_exposes_non_null_rewind_source(self) -> None:
        missing = type("App", (), {})()
        absent = type("App", (), {"rewind": None})()
        present = type("App", (), {"rewind": object()})()
        self.assertNotIn("rewind", available_services(missing))
        self.assertNotIn("rewind", available_services(absent))
        self.assertIn("rewind", available_services(present))
