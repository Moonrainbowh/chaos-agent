from __future__ import annotations

import unittest
from types import SimpleNamespace

from code_agent.interfaces.command_registry import CommandRegistry, REGISTRY
from code_agent.interfaces.input_buffer import InputBuffer
from code_agent.interfaces.picker import (
    PickerSource,
    command_picker_items,
)
from code_agent.interfaces.plugin_picker import plugin_picker_items
from code_agent.interfaces.tui_interactions import TuiInteractions
from code_agent.interfaces.tui_command_dispatch import handle_tui_command
from code_agent.interfaces.tui_commands import parse_tui_command
from code_agent.interfaces.tui_plugin_commands import handle_plugin_command
from code_agent.interfaces.tui_skill_commands import handle_skill_command


class Skills:
    def list(self):
        return (
            SimpleNamespace(
                identifier="review",
                description="Review changes",
                digest="abc",
                sources=(),
            ),
        )


class Plugins:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.values = (
            SimpleNamespace(identifier="reviewer", enabled=False, status="disabled"),
        )

    def list(self):
        self.calls.append(("list", None))
        return self.values

    def status(self, identifier: str | None = None):
        self.calls.append(("status", identifier))
        return self.values

    async def enable(self, identifier: str):
        self.calls.append(("enable", identifier))

    async def disable(self, identifier: str):
        self.calls.append(("disable", identifier))

    async def reload(self):
        self.calls.append(("reload", None))
        return self.values


class PickerApp:
    _pending_approval = None
    _pending_interaction = None
    command_registry = REGISTRY
    skills = Skills()
    mcp = None
    plugins = Plugins()
    checkpoints = object()

    def __init__(self) -> None:
        self.input = InputBuffer()
        self.submitted: list[str] = []

    @staticmethod
    def _columns() -> int:
        return 100

    async def submit(self, text: str) -> bool:
        self.submitted.append(text)
        return True


class PickerHierarchyTests(unittest.IsolatedAsyncioTestCase):
    def test_compound_controls_are_parent_items_and_plugin_items_are_typed(self) -> None:
        services = {"skills", "mcp", "checkpoints", "plugins", "modes", "permissions"}
        items = command_picker_items(REGISTRY.all(), services)
        labels = {item.label for item in items}

        self.assertNotIn("/技能", labels)
        self.assertNotIn("/mcp", labels)
        self.assertNotIn("/检查点", labels)
        self.assertNotIn("/插件", labels)
        self.assertIn("/模式", labels)
        self.assertIn("/权限", labels)

        actions = command_picker_items(
            REGISTRY.all(), services, parent=REGISTRY.resolve("插件")
        )
        self.assertTrue(all(item.source is PickerSource.PLUGIN for item in actions))

    def test_alias_context_opens_second_level_and_plugin_resource_level(self) -> None:
        app = PickerApp()
        controls = TuiInteractions()

        for value, expected in (
            ("/技能 ", "/技能 启用"),
            ("/mcp ", "/mcp enable"),
            ("/checkpoint ", "/检查点 创建"),
            ("/plugin ", "/插件 enable"),
        ):
            app.input.replace(value)
            rows = controls.rows(app)
            self.assertTrue(any(expected in row for row in rows), value)

        app.input.replace("/plugin enable ")
        rows = controls.rows(app)
        self.assertTrue(any("reviewer" in row for row in rows))
        self.assertEqual(controls.picker.selected.source, PickerSource.PLUGIN)

    async def test_keyboard_walks_parent_action_and_dynamic_skill_resource(self) -> None:
        app = PickerApp()
        controls = TuiInteractions()
        app.input.replace("/技能")

        await controls.handle_key(app, "\r")
        self.assertEqual(app.input.text, "/技能 ")
        self.assertEqual(app.submitted, [])

        controls.rows(app)
        controls.picker.move(2)
        await controls.handle_key(app, "\r")
        self.assertEqual(app.input.text, "/技能 启用 ")

        await controls.handle_key(app, "\r")
        self.assertEqual(app.submitted, ["/技能 启用 review"])

    def test_plugin_contribution_and_mode_candidates_use_plugin_source(self) -> None:
        descriptor = SimpleNamespace(
            qualified_id="review.audit",
            description="Audit",
            controller="review",
            plugin_id="reviewer",
            digest="a" * 64,
            generation=2,
        )
        registry = CommandRegistry().with_plugin_modes(
            ("review.focus",)
        ).with_plugin_commands((descriptor,))
        items = command_picker_items(registry.all(), {"plugins", "modes"})
        mode_items = command_picker_items(
            registry.all(), {"plugins", "modes"}, parent=registry.resolve("模式")
        )

        self.assertFalse(any(item.identifier == "review.audit" for item in items))
        contribution = registry.resolve("review.audit")
        plugin_mode = next(item for item in mode_items if item.identifier == "模式:review.focus")
        self.assertEqual(contribution.visibility.value, "internal")
        self.assertEqual(plugin_mode.source, PickerSource.PLUGIN)

    def test_plugin_resource_picker_uses_controller_snapshot(self) -> None:
        item = plugin_picker_items(Plugins(), "enable")[0]

        self.assertEqual(item.completion, "/插件 enable reviewer")
        self.assertEqual(item.source, PickerSource.PLUGIN)


class PluginCommandHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_management_actions_delegate_to_generic_plugin_controller(self) -> None:
        class App:
            plugins = Plugins()
            rows: list[tuple[object, str]] = []

            @classmethod
            def _append(cls, kind: object, value: object) -> None:
                cls.rows.append((kind, str(value)))

        app = App()
        for instruction in (
            "list",
            "status reviewer",
            "enable reviewer",
            "disable reviewer",
            "reload",
        ):
            self.assertTrue(await handle_plugin_command(app, instruction))

        self.assertEqual(
            app.plugins.calls,
            [
                ("list", None),
                ("status", "reviewer"),
                ("enable", "reviewer"),
                ("disable", "reviewer"),
                ("reload", None),
            ],
        )

    async def test_parsed_plugin_control_reaches_builtin_dispatch(self) -> None:
        class App:
            plugins = Plugins()
            rows: list[str] = []

            @classmethod
            def _append(cls, _kind: object, value: object) -> None:
                cls.rows.append(str(value))

        app = App()
        parsed = parse_tui_command("/plugin enable reviewer")

        self.assertTrue(await handle_tui_command(app, parsed))
        self.assertEqual(app.plugins.calls, [("enable", "reviewer")])


class SkillListFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_active_and_error_filters_render_distinct_snapshots(self) -> None:
        class Activation:
            @staticmethod
            def active():
                return (SimpleNamespace(identifier="active-review"),)

        class SkillController:
            @staticmethod
            def list():
                return (SimpleNamespace(identifier="all-review"),)

            @staticmethod
            def activation(thread_id: str):
                assert thread_id == "thread-1"
                return Activation()

            @staticmethod
            def errors():
                return ("conflict: conflicting digests",)

        class App:
            skills = SkillController()
            current_thread_id = "thread-1"
            rows: list[str] = []

            @classmethod
            def _append(cls, _kind: object, value: object) -> None:
                cls.rows.append(str(value))

        app = App()
        for option in ("--all", "--active", "--errors"):
            self.assertTrue(await handle_skill_command(app, f"list {option}"))

        self.assertEqual(
            app.rows,
            ["all-review", "active-review", "conflict: conflicting digests"],
        )
        self.assertFalse(await handle_skill_command(app, "list --unknown"))
        self.assertEqual(app.rows[-1], "Skill list filter is invalid")


if __name__ == "__main__":
    unittest.main()
