from __future__ import annotations

import hashlib
import unittest
from types import SimpleNamespace

from code_agent.core.models import ToolDefinition
from code_agent.plugins.models import (
    ActionProposal,
    CommandContribution,
    EventSubscription,
    PluginContributions,
    PluginManifest,
    PluginRisk,
    ToolContribution,
)
from code_agent.plugins.registry import (
    ContributionSnapshot,
    PluginHost,
    RegisteredContribution,
)
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent_win.host_composition import (
    PluginRuntimeBindings,
    _configured_mcp_targets,
)
from code_agent_win.plugin_runtime import (
    PluginCommandController,
    PluginToolBridge,
    plugin_event_risks,
)
from code_agent_win.subagents import RestrictedDispatcher
from code_agent_win.ui_composition import (
    plugin_command_registry,
    refresh_plugin_surfaces,
)


def _snapshot(revision: str) -> ContributionSnapshot:
    tool = ToolContribution(
        f"{revision}_tool",
        f"{revision} tool",
        "read_file",
        PluginRisk.READ,
    )
    command = CommandContribution(
        f"{revision}_cmd", f"{revision} command", "task"
    )
    event = EventSubscription(
        f"{revision}_event",
        ("task_created",),
        action=ActionProposal("read_file", risk=PluginRisk.WRITE),
    )
    contributions = PluginContributions(
        tools=(tool,), commands=(command,), events=(event,)
    )
    manifest = PluginManifest(
        "live-plugin",
        "live",
        "1.0.0",
        "1",
        hashlib.sha256(revision.encode("utf-8")).hexdigest(),
        "plugin.json",
        True,
        True,
        contributions,
    )
    registered = tuple(
        RegisteredContribution(
            manifest.identifier,
            manifest.namespace,
            kind,
            value.identifier,
            value,
        )
        for kind, value in (
            ("tool", tool),
            ("command", command),
            ("event", event),
        )
    )
    return ContributionSnapshot((manifest,), registered)


class _LiveDispatcher:
    def __init__(self, bridge: PluginToolBridge, risks: dict[str, str]) -> None:
        self.plugins = bridge
        self.policy = ActionPolicy(PolicyConfig(mcp_risks=risks))

    def tools(self) -> tuple[ToolDefinition, ...]:
        return (
            ToolDefinition("read_file", "read", {}),
            ToolDefinition("write_file", "write", {}),
            *self.plugins.definitions(),
        )


class _ModeControl:
    def __init__(self) -> None:
        self.identifiers: tuple[str, ...] | None = None

    async def refresh(self, identifiers: tuple[str, ...]) -> None:
        self.identifiers = identifiers


class _Tui:
    def __init__(self, host: PluginHost) -> None:
        self._run_task = None
        self.command_registry = plugin_command_registry(host)


class _ActiveTask:
    @staticmethod
    def done() -> bool:
        return False


def _live_runtime() -> SimpleNamespace:
    host = PluginHost(_snapshot("old"))
    bridge = PluginToolBridge(host)
    risks = {"read_file": "read"}
    risks.update(bridge.risk_map())
    risks.update(plugin_event_risks(host))
    dispatcher = _LiveDispatcher(bridge, risks)
    bindings = PluginRuntimeBindings(dispatcher, host, bridge)
    restricted = bindings.bind(
        RestrictedDispatcher(dispatcher, ()),
        lambda: tuple(tool.name for tool in bridge.definitions()),
    )
    modes = _ModeControl()
    tui = _Tui(host)
    controller = PluginCommandController(
        host,
        discover=lambda: (_snapshot("new"), ()),
        on_change=lambda: refresh_plugin_surfaces(host, modes, tui, bindings.refresh),
    )
    controller.attach(tui)
    tui._run_task = _ActiveTask()
    return SimpleNamespace(
        bridge=bridge,
        risks=risks,
        dispatcher=dispatcher,
        bindings=bindings,
        restricted=restricted,
        modes=modes,
        tui=tui,
        controller=controller,
    )


class PluginLiveWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_plugin_discovery_target_set_includes_configured_mcp_tools(self) -> None:
        mcp = SimpleNamespace(
            status=lambda: (
                SimpleNamespace(
                    name="docs", tool_risks={"search": "read", "edit": "write"}
                ),
            )
        )

        self.assertEqual(
            _configured_mcp_targets(mcp),
            ("mcp.docs.search", "mcp.docs.edit"),
        )

    async def test_reload_and_disable_refresh_commands_tools_and_policy(self) -> None:
        runtime = _live_runtime()

        self.assertIsNotNone(runtime.tui.command_registry.resolve("live.old_cmd"))
        self.assertEqual(
            tuple(tool.name for tool in runtime.restricted.tools()),
            ("live.old_tool",),
        )

        self.assertEqual(await runtime.controller.reload(), "Plugin reload staged")
        self.assertIsNotNone(runtime.tui.command_registry.resolve("live.old_cmd"))
        self.assertEqual(
            tuple(tool.name for tool in runtime.restricted.tools()),
            ("live.old_tool",),
        )

        self.assertTrue(await runtime.controller.apply_staged(idle=True))

        self.assertIsNone(runtime.tui.command_registry.resolve("live.old_cmd"))
        self.assertIsNotNone(runtime.tui.command_registry.resolve("live.new_cmd"))
        self.assertEqual(
            tuple(tool.name for tool in runtime.restricted.tools()),
            ("live.new_tool",),
        )
        future = runtime.bindings.bind(
            RestrictedDispatcher(runtime.dispatcher, ()),
            lambda: tuple(tool.name for tool in runtime.bridge.definitions()),
        )
        self.assertEqual(
            tuple(tool.name for tool in future.tools()),
            ("live.new_tool",),
        )
        self.assertNotIn("live.old_tool", runtime.risks)
        self.assertEqual(runtime.risks["live.new_tool"], "read")
        self.assertNotIn("plugin_event.live-plugin.old_event", runtime.risks)
        self.assertEqual(
            runtime.risks["plugin_event.live-plugin.new_event"], "write"
        )
        self.assertEqual(runtime.modes.identifiers, ())

        runtime.tui._run_task = None
        await runtime.controller.disable("live-plugin")

        self.assertIsNone(runtime.tui.command_registry.resolve("live.new_cmd"))
        self.assertEqual(runtime.restricted.tools(), ())
        self.assertEqual(future.tools(), ())
        self.assertNotIn("live.new_tool", runtime.risks)
        self.assertNotIn("plugin_event.live-plugin.new_event", runtime.risks)
        self.assertEqual(runtime.risks["read_file"], "read")

    async def test_restricted_allowlist_replace_and_update_are_validated(self) -> None:
        dispatcher = _LiveDispatcher(PluginToolBridge(PluginHost()), {})
        restricted = RestrictedDispatcher(
            dispatcher, ("read_file", "delegate_agent")
        )

        restricted.update_allowed(add=("write_file",), remove=("read_file",))

        self.assertEqual(
            tuple(tool.name for tool in restricted.tools()),
            ("write_file",),
        )
        with self.assertRaises(TypeError):
            restricted.replace_allowed("read_file")  # type: ignore[arg-type]
        self.assertEqual(
            tuple(tool.name for tool in restricted.tools()),
            ("write_file",),
        )


if __name__ == "__main__":
    unittest.main()
