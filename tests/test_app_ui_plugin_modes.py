from __future__ import annotations

import unittest
from dataclasses import replace

from code_agent.core.limits import EngineLimits
from code_agent.interfaces.mode_control import ModeSummary
from code_agent.orchestration.models import (
    AgentMode,
    ModeDefinition,
    ModeSnapshot,
    ReasoningEffort,
)
from code_agent.orchestration.plugin_extensions import PluginModeSnapshot
from code_agent_win.app_ui import PluginModeControl


class _BaseModes:
    current = ModeSummary("medium", "model", "medium")

    def __init__(self) -> None:
        self.used: list[tuple[str, bool]] = []

    def list(self):
        return (self.current,)

    async def use(self, name: str, *, idle: bool):
        self.used.append((name, idle))
        return self.current


class _Catalog:
    def __init__(self, mode: PluginModeSnapshot) -> None:
        self.mode = mode

    def resolve(self, identifier: str) -> PluginModeSnapshot:
        if identifier != self.mode.identifier:
            raise KeyError(identifier)
        return self.mode


def _plugin_mode_snapshots() -> tuple[ModeSnapshot, PluginModeSnapshot]:
    base = ModeSnapshot(
        ModeDefinition(
            AgentMode.MEDIUM,
            "profile",
            "default",
            ("read_file", "write_file"),
            ReasoningEffort.MEDIUM,
            EngineLimits(),
            "base mode",
        ),
        "model",
        None,
        "a" * 64,
    )
    contributed = PluginModeSnapshot(
        "review.focus",
        "review",
        "b" * 64,
        1,
        base,
        "default",
        ("read_file",),
        ReasoningEffort.HIGH,
    )
    return base, contributed


class PluginModeRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_identifier_refresh_reverts_base_before_clearing(self) -> None:
        _, contributed = _plugin_mode_snapshots()
        applied: list[ModeSnapshot] = []

        async def apply(snapshot: ModeSnapshot) -> None:
            applied.append(snapshot)

        digests: set[str] = set()
        base_modes = _BaseModes()
        catalog = _Catalog(contributed)
        control = PluginModeControl(
            base_modes,
            catalog,
            (contributed.identifier,),
            apply,
            digests,
        )
        selected = await control.use(contributed.identifier, idle=True)
        selected_digest = applied[0].digest
        catalog.mode = replace(contributed, digest="c" * 64, generation=2)

        await control.refresh((contributed.identifier,))

        self.assertEqual(selected.name, contributed.identifier)
        self.assertEqual(control.current.name, "medium")
        self.assertNotIn(selected_digest, digests)
        self.assertEqual(base_modes.used, [("medium", True)])
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0].definition.tool_names, ("read_file",))


if __name__ == "__main__":
    unittest.main()
