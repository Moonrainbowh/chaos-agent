from __future__ import annotations

import unittest

from code_agent.interfaces.command_registry import CommandVisibility, REGISTRY
from code_agent.interfaces.tui_commands import TuiCommandKind, parse_tui_command


class TuiCommandTests(unittest.TestCase):
    def test_parses_chinese_and_english_commands(self) -> None:
        self.assertEqual(parse_tui_command("/任务").command.kind, TuiCommandKind.TASKS)
        self.assertEqual(parse_tui_command("/evidence").command.kind, TuiCommandKind.EVIDENCE)
        self.assertEqual(parse_tui_command("/accept T-042").command.kind, TuiCommandKind.ACCEPT)
        self.assertEqual(parse_tui_command("/接受 T-042").command.task_id, "T-042")
        self.assertEqual(parse_tui_command("/pause T-042").error, "unknown or unavailable slash command")
        self.assertEqual(
            parse_tui_command("/模式 思考 xhigh").command.instruction,
            "思考 xhigh",
        )
        self.assertEqual(parse_tui_command("/权限 unrestricted").command.kind, TuiCommandKind.PERMISSION)
        self.assertEqual(parse_tui_command("/流程 失败").command.kind, TuiCommandKind.WORKFLOW)
        self.assertEqual(parse_tui_command("/flow review").command.instruction, "review")
        self.assertEqual(
            parse_tui_command("/技能 启用 review").command.kind,
            TuiCommandKind.SKILL,
        )
        self.assertEqual(
            parse_tui_command("/mcp restart docs").command.kind,
            TuiCommandKind.MCP,
        )
        self.assertEqual(
            parse_tui_command("/plugin enable reviewer").command.kind,
            TuiCommandKind.PLUGIN_CONTROL,
        )
        self.assertFalse(parse_tui_command("/does-not-exist").is_command)
        self.assertEqual(parse_tui_command("/does-not-exist").error, "unknown or unavailable slash command")

    def test_registry_drives_parse_and_availability(self) -> None:
        visible = REGISTRY.filter("/模", {"runtime_selection"})
        spec, arguments, error = REGISTRY.parse(
            "/模式 思考 high", {"runtime_selection"}
        )

        self.assertEqual(visible[0].name, "模式")
        self.assertEqual(spec.name, "模式")
        self.assertEqual(arguments, ("思考", "high"))
        self.assertIsNone(error)
        self.assertEqual(REGISTRY.parse("/模型", {"modes"})[2], "unknown or unavailable slash command")

    def test_every_registered_name_and_alias_resolves_to_its_own_command(self) -> None:
        services = {
            "sessions", "history", "tasks", "evidence", "modes", "permissions", "workflows",
            "skills", "mcp", "plugins", "attachments",
        }

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

    def test_checkpoint_and_rewind_have_chinese_and_english_aliases(self) -> None:
        services = {"checkpoints"}

        self.assertEqual(
            parse_tui_command("/checkpoint list", services).command.kind,
            TuiCommandKind.CHECKPOINT,
        )
        self.assertEqual(
            parse_tui_command("/检查点 创建 发布前", services).command.instruction,
            "创建 发布前",
        )
        self.assertEqual(
            parse_tui_command("/rewind", services).command.kind,
            TuiCommandKind.REWIND,
        )
        self.assertIsNone(parse_tui_command("/回退", services).command.instruction)
        self.assertEqual(
            parse_tui_command(f"/rewind {'3' * 32}", services).command.instruction,
            "3" * 32,
        )

    def test_registry_contains_only_the_confirmed_common_commands(self) -> None:
        self.assertEqual(
            tuple(spec.name for spec in REGISTRY.all()),
            (
                "帮助", "状态", "新建", "会话", "任务", "差异", "附件",
                "回退", "模式", "权限", "退出", "清屏", "恢复", "接受",
                "证据", "检查点", "流程", "技能", "mcp", "插件",
            ),
        )

    def test_registry_has_an_exact_compact_primary_surface(self) -> None:
        self.assertEqual(
            tuple(spec.name for spec in REGISTRY.primary()),
            (
                "帮助", "状态", "新建", "会话", "任务", "差异", "附件",
                "回退", "模式", "权限", "退出",
            ),
        )
        self.assertTrue(
            all(spec.visibility is CommandVisibility.ADVANCED for spec in REGISTRY.all()[11:])
        )

    def test_advanced_commands_remain_directly_parseable_but_not_filter_candidates(self) -> None:
        self.assertEqual(parse_tui_command("/清屏").command.kind, TuiCommandKind.CLEAR)
        self.assertEqual(parse_tui_command("/证据 T-042").command.kind, TuiCommandKind.EVIDENCE)
        self.assertEqual(REGISTRY.filter("/证", {"evidence"}), ())

    def test_attachment_command_preserves_windows_paths_verbatim(self) -> None:
        command = parse_tui_command(
            r'/attach "C:\Screenshots\UI error.png"',
            {"attachments"},
        ).command

        self.assertEqual(command.kind, TuiCommandKind.ATTACHMENT)
        self.assertEqual(command.instruction, r'"C:\Screenshots\UI error.png"')

    def test_checkpoint_declares_list_and_create_actions(self) -> None:
        spec = REGISTRY.resolve("checkpoint")

        self.assertEqual(
            tuple(action.name for action in spec.actions),
            ("列表", "创建"),
        )

    def test_mode_declares_three_runtime_selection_actions(self) -> None:
        mode = REGISTRY.resolve("模式")

        self.assertEqual(
            tuple(
                action.name
                for action in mode.actions
                if action.visibility.value == "primary"
            ),
            ("代理", "模型", "思考"),
        )
        self.assertEqual(
            parse_tui_command("/mode topology team").command.action,
            "代理",
        )
        self.assertEqual(
            parse_tui_command("/模式 模型 sol").command.instruction,
            "模型 sol",
        )
        self.assertEqual(
            parse_tui_command("/模式 思考").error,
            "command action argument is required",
        )

    def test_session_actions_and_hidden_compatibility_aliases_parse_canonically(self) -> None:
        self.assertEqual(
            tuple(action.name for action in REGISTRY.resolve("会话").actions),
            ("历史", "在线", "重命名", "发送", "接收", "待处理", "接受", "拒绝"),
        )
        self.assertEqual(parse_tui_command("/会话 在线").command.action, "在线")
        self.assertEqual(parse_tui_command("/list-agents").command.action, "在线")
        self.assertEqual(parse_tui_command("/peers").command.action, "在线")
        renamed = parse_tui_command("/rename worker one").command
        self.assertEqual(
            (renamed.action, renamed.instruction),
            ("重命名", "重命名 worker one"),
        )
        self.assertIsNone(REGISTRY.resolve("list-agents"))
        self.assertIsNone(REGISTRY.resolve("peers"))
        self.assertIsNone(REGISTRY.resolve("rename"))

    def test_session_action_availability_keeps_history_without_peers(self) -> None:
        self.assertIsNone(parse_tui_command("/会话 历史", {"sessions"}).error)
        self.assertEqual(
            parse_tui_command("/会话 在线", {"sessions"}).error,
            "unknown or unavailable slash command action",
        )
        self.assertIsNone(parse_tui_command("/会话 在线", {"peers"}).error)

    def test_permission_declares_direct_secondary_choices(self) -> None:
        permission = REGISTRY.resolve("权限")

        self.assertEqual(
            tuple(action.name for action in permission.actions),
            ("unrestricted", "plan", "ask", "auto", "elevated", "full-local"),
        )
        self.assertEqual(
            parse_tui_command("/permission unrestricted").command.kind,
            TuiCommandKind.PERMISSION,
        )

    def test_skill_and_mcp_actions_validate_required_arguments(self) -> None:
        self.assertEqual(
            parse_tui_command("/技能 启用").error,
            "command action argument is required",
        )
        self.assertEqual(
            parse_tui_command("/mcp restart").error,
            "command action argument is required",
        )
        self.assertEqual(
            parse_tui_command("/技能 unknown").error,
            "unknown slash command action",
        )

    def test_plugin_control_actions_and_aliases_are_registered(self) -> None:
        plugin = REGISTRY.resolve("plugin")

        self.assertEqual(plugin.name, "插件")
        self.assertEqual(
            tuple(action.name for action in plugin.actions),
            ("list", "status", "enable", "disable", "reload"),
        )
        self.assertEqual(
            parse_tui_command("/插件 状态 reviewer").command.action,
            "status",
        )
        self.assertEqual(
            parse_tui_command("/plugins disable reviewer").command.kind,
            TuiCommandKind.PLUGIN_CONTROL,
        )
        self.assertEqual(
            parse_tui_command("/plugin enable").error,
            "command action argument is required",
        )
