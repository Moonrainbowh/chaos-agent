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
        self.assertEqual(parse_tui_command("/模式 ultra").command.instruction, "ultra")
        self.assertFalse(parse_tui_command("/does-not-exist").is_command)
        self.assertEqual(parse_tui_command("/does-not-exist").error, "unknown or unavailable slash command")

    def test_registry_drives_parse_and_availability(self) -> None:
        visible = REGISTRY.filter("/模", {"modes"})
        spec, arguments, error = REGISTRY.parse("/模式 high", {"modes"})

        self.assertEqual(visible[0].name, "模式")
        self.assertEqual(spec.name, "模式")
        self.assertEqual(arguments, ("high",))
        self.assertIsNone(error)
        self.assertEqual(REGISTRY.parse("/模型", {"modes"})[2], "unknown or unavailable slash command")

    def test_every_registered_name_and_alias_resolves_to_its_own_command(self) -> None:
        services = {"sessions", "history", "tasks", "evidence", "modes"}

        for expected in REGISTRY.available(services):
            for name in (expected.name, *expected.aliases):
                with self.subTest(name=name):
                    spec, arguments, error = REGISTRY.parse("/" + name, services)
                    self.assertIs(spec, expected)
                    self.assertEqual(arguments, ())
                    self.assertIsNone(error)

    def test_rejects_arguments_and_unknown_compound_actions(self) -> None:
        extra = parse_tui_command("/状态 unexpected")
        unknown_action = parse_tui_command("/模式 extreme")

        self.assertEqual(extra.error, "command does not accept arguments")
        self.assertEqual(unknown_action.error, "unknown slash command action")

    def test_preserves_optional_command_arguments(self) -> None:
        help_command = parse_tui_command("/帮助 模式").command
        evidence_command = parse_tui_command("/证据 T-042").command

        self.assertEqual(help_command.instruction, "模式")
        self.assertEqual(evidence_command.instruction, "T-042")

    def test_registry_contains_only_the_confirmed_common_commands(self) -> None:
        self.assertEqual(
            tuple(spec.name for spec in REGISTRY.all()),
            (
                "帮助", "状态", "清屏", "退出",
                "新建", "会话", "恢复",
                "任务", "暂停", "继续", "停止", "接受",
                "差异", "证据", "模式",
            ),
        )

    def test_mode_declares_direct_secondary_choices(self) -> None:
        mode = REGISTRY.resolve("模式")

        self.assertEqual(
            tuple(action.name for action in mode.actions),
            ("low", "medium", "high", "ultra"),
        )
        self.assertEqual(parse_tui_command("/mode high").command.kind, TuiCommandKind.MODE)
        self.assertEqual(parse_tui_command("/模式 high").command.instruction, "high")
