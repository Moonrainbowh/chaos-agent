from __future__ import annotations

import unittest

from code_agent.core.limits import EngineLimits
from code_agent.interfaces.mode_control import ModeControl
from code_agent.orchestration.models import (
    AgentMode,
    ModeDefinition,
    ModeSnapshot,
    ReasoningEffort,
)


def _snapshot(mode: AgentMode, model: str) -> ModeSnapshot:
    definition = ModeDefinition(
        mode,
        mode.value,
        mode.value,
        ("read_file",),
        ReasoningEffort(mode.value if mode is not AgentMode.ULTRA else "xhigh"),
        EngineLimits(),
        mode.value,
    )
    return ModeSnapshot(definition, model, None, "a" * 64)


class ModeControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_lists_modes_and_switches_at_idle_boundary(self) -> None:
        snapshots = {
            mode: _snapshot(mode, f"model-{mode.value}")
            for mode in AgentMode
        }
        applied = []

        async def apply(snapshot: ModeSnapshot) -> None:
            applied.append(snapshot)

        control = ModeControl(snapshots, AgentMode.MEDIUM, apply)

        self.assertEqual(control.current.name, "medium")
        self.assertEqual(control.current.model, "model-medium")
        self.assertEqual(tuple(item.name for item in control.list()), ("low", "medium", "high", "ultra"))

        selected = await control.use("high", idle=True)

        self.assertEqual(selected.name, "high")
        self.assertEqual(control.current.model, "model-high")
        self.assertEqual(applied, [snapshots[AgentMode.HIGH]])

    async def test_rejects_running_or_unknown_mode_without_changing_current(self) -> None:
        snapshots = {
            mode: _snapshot(mode, f"model-{mode.value}")
            for mode in AgentMode
        }
        control = ModeControl(snapshots, AgentMode.MEDIUM, lambda _: None)

        with self.assertRaisesRegex(RuntimeError, "only when idle"):
            await control.use("high", idle=False)
        with self.assertRaisesRegex(ValueError, "unknown agent mode"):
            await control.use("extreme", idle=True)

        self.assertEqual(control.current.name, "medium")
