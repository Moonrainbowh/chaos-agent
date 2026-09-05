from __future__ import annotations

import unittest

from code_agent.interfaces.command_registry import CommandVisibility, REGISTRY
from code_agent.interfaces.tui_commands import TuiCommandKind, parse_tui_command


class TuiCommandTests(unittest.TestCase):
    def test_parses_chinese_and_english_commands(self) -> None:
        self.assertEqual(parse_tui_command("/tasks").command.kind, TuiCommandKind.TASKS)
        self.assertEqual(parse_tui_command("/evidence").command.kind, TuiCommandKind.EVIDENCE)
        self.assertEqual(parse_tui_command("/accept T-042").command.kind, TuiCommandKind.ACCEPT)
        self.assertEqual(parse_tui_command("/accept T-042").command.task_id, "T-042")
        self.assertEqual(parse_tui_command("/pause T-042").error, "unknown or unavailable slash command")
        self.assertEqual(
            parse_tui_command("/mode effort xhigh").command.instruction,
            "effort xhigh",
        )
        self.assertEqual(parse_tui_command("/permission unrestricted").command.kind, TuiCommandKind.PERMISSION)
        self.assertEqual(parse_tui_command("/flow failure").command.kind, TuiCommandKind.WORKFLOW)
        self.assertEqual(parse_tui_command("/flow review").command.instruction, "review")
        self.assertEqual(
            parse_tui_command("/skill enable review").command.kind,
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
        visible = REGISTRY.filter("/mod", {"runtime_selection"})
        spec, arguments, error = REGISTRY.parse(
            "/mode effort high", {"runtime_selection"}
        )

        self.assertEqual(tuple(item.name for item in visible), ("model", "mode"))
        self.assertEqual(spec.name, "mode")
        self.assertEqual(arguments, ("effort", "high"))
        self.assertIsNone(error)
        self.assertEqual(REGISTRY.parse("/model", {"modes"})[2], "unknown or unavailable slash command")

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
        extra = parse_tui_command("/status unexpected")
        unknown_action = parse_tui_command("/mode extreme")

        self.assertEqual(extra.error, "command does not accept arguments")
        self.assertEqual(unknown_action.error, "unknown slash command action")

    def test_preserves_optional_command_arguments(self) -> None:
        help_command = parse_tui_command("/help mode").command
        evidence_command = parse_tui_command("/evidence T-042").command

        self.assertEqual(help_command.instruction, "mode")
        self.assertEqual(evidence_command.instruction, "T-042")

    def test_checkpoint_and_rewind_have_chinese_and_english_aliases(self) -> None:
        services = {"checkpoints"}

        self.assertEqual(
            parse_tui_command("/checkpoint list", services).command.kind,
            TuiCommandKind.CHECKPOINT,
        )
        self.assertEqual(
            parse_tui_command("/checkpoint create pre-release", services).command.instruction,
            "create pre-release",
        )
        self.assertEqual(
            parse_tui_command("/rewind", services).command.kind,
            TuiCommandKind.REWIND,
        )
        self.assertIsNone(parse_tui_command("/undo", services).command.instruction)
        self.assertEqual(
            parse_tui_command(f"/rewind {'3' * 32}", services).command.instruction,
            "3" * 32,
        )

    def test_registry_contains_only_the_confirmed_common_commands(self) -> None:
        self.assertEqual(
            tuple(spec.name for spec in REGISTRY.all()),
            (
                "theme",
                "clear", "compact", "cost", "status", "doctor", "exit",
                "diff", "map", "review", "test", "rewind", "attach", "model", "mode",
                "effort", "permission", "mcp", "plugin", "tasks", "help",
                "sessions", "new", "restore", "accept",
                "evidence", "checkpoint", "flow", "skill",
            ),
        )

    def test_registry_has_an_exact_compact_primary_surface(self) -> None:
        self.assertEqual(
            tuple(spec.name for spec in REGISTRY.primary()),
            (
                "clear", "compact", "cost", "status", "doctor", "exit",
                "diff", "map", "review", "test", "rewind", "attach", "model", "mode",
                "effort", "permission", "mcp", "plugin", "tasks",
            ),
        )
        self.assertTrue(
            all(
                spec.visibility is not CommandVisibility.PRIMARY
                for spec in REGISTRY.all()
                if spec.name not in {item.name for item in REGISTRY.primary()}
            )
        )

    def test_advanced_commands_remain_directly_parseable_but_not_filter_candidates(self) -> None:
        self.assertEqual(parse_tui_command("/new").command.kind, TuiCommandKind.NEW)
        self.assertEqual(parse_tui_command("/evidence T-042").command.kind, TuiCommandKind.EVIDENCE)
        self.assertEqual(REGISTRY.filter("/restore", {"history"}), ())

    def test_colon_is_the_primary_prefix_and_slash_remains_compatible(self) -> None:
        self.assertEqual(parse_tui_command(":status").command.kind, TuiCommandKind.STATUS)
        self.assertEqual(parse_tui_command("/status").command.kind, TuiCommandKind.STATUS)
        self.assertEqual(REGISTRY.resolve("diff").visibility, CommandVisibility.PRIMARY)

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
            ("list", "create"),
        )

    def test_mode_declares_three_task_behavior_actions(self) -> None:
        mode = REGISTRY.resolve("mode")

        self.assertEqual(
            tuple(
                action.name
                for action in mode.actions
                if action.visibility.value == "primary"
            ),
            ("ask", "code", "plan"),
        )
        self.assertEqual(
            parse_tui_command("/mode ask").command.action,
            "ask",
        )
        self.assertEqual(parse_tui_command("/m sol").command.kind, TuiCommandKind.MODEL)
        self.assertEqual(parse_tui_command("/effort high").command.kind, TuiCommandKind.EFFORT)
        self.assertEqual(
            parse_tui_command("/mode model sol").command.instruction,
            "model sol",
        )
        self.assertEqual(
            parse_tui_command("/mode effort").error,
            "command action argument is required",
        )

    def test_map_declares_all_shared_graph_product_actions(self) -> None:
        semantic_map = REGISTRY.resolve("map")

        self.assertEqual(
            tuple(action.name for action in semantic_map.actions),
            (
                "overview", "context", "impact", "tests", "risk",
                "review", "refactor", "locate", "dead-code",
            ),
        )
        parsed = parse_tui_command(
            '/map impact "src/pkg/file with space.py"'
        ).command
        self.assertEqual(parsed.kind, TuiCommandKind.MAP)
        self.assertEqual(parsed.action, "impact")
        self.assertEqual(
            parsed.instruction,
            'impact "src/pkg/file with space.py"',
        )

    def test_session_actions_and_hidden_compatibility_aliases_parse_canonically(self) -> None:
        self.assertEqual(
            tuple(action.name for action in REGISTRY.resolve("sessions").actions),
            ("history", "online", "rename", "send", "inbound", "inbox", "accept", "refuse"),
        )
        self.assertEqual(parse_tui_command("/sessions online").command.action, "online")
        self.assertEqual(parse_tui_command("/list-agents").command.action, "online")
        self.assertEqual(parse_tui_command("/peers").command.action, "online")
        renamed = parse_tui_command("/rename worker one").command
        self.assertEqual(
            (renamed.action, renamed.instruction),
            ("rename", "rename worker one"),
        )
        self.assertIsNone(REGISTRY.resolve("list-agents"))
        self.assertIsNone(REGISTRY.resolve("peers"))
        self.assertIsNone(REGISTRY.resolve("rename"))

    def test_session_action_availability_keeps_history_without_peers(self) -> None:
        self.assertIsNone(parse_tui_command("/sessions history", {"sessions"}).error)
        self.assertEqual(
            parse_tui_command("/sessions online", {"sessions"}).error,
            "unknown or unavailable slash command action",
        )
        self.assertIsNone(parse_tui_command("/sessions online", {"peers"}).error)

    def test_permission_declares_direct_secondary_choices(self) -> None:
        permission = REGISTRY.resolve("permission")

        self.assertEqual(
            tuple(action.name for action in permission.actions),
            (
                "auto", "plan", "ask", "unrestricted", "elevated", "full-local",
                "allow-command", "rules", "revoke",
            ),
        )
        allowed = parse_tui_command("/permission allow-command python -m pytest").command
        self.assertEqual(allowed.action, "allow-command")
        self.assertEqual(allowed.instruction, "allow-command python -m pytest")
        self.assertEqual(
            parse_tui_command("/permission unrestricted").command.kind,
            TuiCommandKind.PERMISSION,
        )

    def test_skill_and_mcp_actions_validate_required_arguments(self) -> None:
        self.assertEqual(
            parse_tui_command("/skill enable").error,
            "command action argument is required",
        )
        self.assertEqual(
            parse_tui_command("/mcp restart").error,
            "command action argument is required",
        )
        self.assertEqual(
            parse_tui_command("/skill unknown").error,
            "unknown slash command action",
        )

    def test_plugin_control_actions_and_aliases_are_registered(self) -> None:
        plugin = REGISTRY.resolve("plugin")

        self.assertEqual(plugin.name, "plugin")
        self.assertEqual(
            tuple(action.name for action in plugin.actions),
            ("list", "status", "enable", "disable", "reload"),
        )
        self.assertEqual(
            parse_tui_command("/plugin status reviewer").command.action,
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
