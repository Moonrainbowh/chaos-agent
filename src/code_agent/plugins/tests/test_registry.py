from __future__ import annotations

import unittest

from code_agent.core.limits import EngineLimits
from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.orchestration.models import AgentMode
from code_agent.plugins.manifest import PluginTrustStore, manifest_digest, parse_manifest
from code_agent.plugins.models import PluginRisk
from code_agent.plugins.registry import PluginHost, PluginRegistryBuilder

from test_manifest import manifest_data


def _modes() -> ModeRegistry:
    tools = {mode: ("read_file", "write_file") for mode in AgentMode}
    limits = {mode: EngineLimits() for mode in AgentMode}
    profiles = {mode: mode.value for mode in AgentMode}
    return ModeRegistry(standard_mode_definitions(profiles, tools_by_mode=tools, limits_by_mode=limits))


def _plugin(raw=None, *, approved=True):
    raw = manifest_data() if raw is None else raw
    raw["digest"] = manifest_digest(raw)
    return parse_manifest(raw, "plugin.json", PluginTrustStore(), host_api="1", approved=approved)


class PluginRegistryTests(unittest.TestCase):
    def builder(self) -> PluginRegistryBuilder:
        return PluginRegistryBuilder(
            _modes(),
            host_actions=("read_file", "write_file"),
            host_action_risks={"read_file": PluginRisk.READ, "write_file": PluginRisk.WRITE},
            controllers=("review",),
        )

    def test_builds_immutable_qualified_contributions(self) -> None:
        snapshot = self.builder().build((_plugin(),))

        self.assertEqual(snapshot.errors, ())
        self.assertEqual(snapshot.contributions[0].qualified_id, "reviewer.inspect")
        self.assertEqual({item.kind for item in snapshot.contributions}, {"tool", "event"})

    def test_conflicts_and_risk_downgrades_isolate_entire_plugin(self) -> None:
        conflict = manifest_data()
        conflict["namespace"] = "core"
        snapshot = self.builder().build((_plugin(conflict),))
        self.assertEqual(snapshot.contributions, ())
        self.assertIn("namespace conflict", snapshot.errors[0])

        downgrade = manifest_data()
        downgrade["contributions"]["tools"][0]["target"] = "write_file"  # type: ignore[index]
        snapshot = self.builder().build((_plugin(downgrade),))
        self.assertEqual(snapshot.contributions, ())
        self.assertIn("cannot lower host risk", snapshot.errors[0])

    def test_mode_and_agent_tools_can_only_narrow_base_mode(self) -> None:
        raw = manifest_data()
        raw["contributions"]["modes"] = [  # type: ignore[index]
            {
                "id": "review-mode",
                "base_mode": "medium",
                "prompt_policy": "review",
                "tool_names": ["unknown_tool"],
                "reasoning_effort": None,
            }
        ]
        snapshot = self.builder().build((_plugin(raw),))

        self.assertEqual(snapshot.contributions, ())
        self.assertIn("must narrow", snapshot.errors[0])

    def test_snapshot_applies_only_when_idle_and_revocation_is_immediate(self) -> None:
        snapshot = self.builder().build((_plugin(),))
        host = PluginHost()
        host.stage(snapshot)

        self.assertFalse(host.apply(task_active=True))
        self.assertEqual(host.contributions(), ())
        self.assertTrue(host.apply(task_active=False))
        self.assertTrue(host.contributions())
        self.assertTrue(host.revoke("review-helper"))
        self.assertEqual(host.contributions(), ())

    def test_event_action_proposal_must_resolve_during_registration(self) -> None:
        raw = manifest_data()
        raw["contributions"]["events"][0]["ui"] = None  # type: ignore[index]
        raw["contributions"]["events"][0]["action"] = {  # type: ignore[index]
            "target": "unknown_action",
            "arguments": {},
        }

        snapshot = self.builder().build((_plugin(raw),))

        self.assertEqual(snapshot.contributions, ())
        self.assertIn("unknown action target", snapshot.errors[0])


if __name__ == "__main__":
    unittest.main()
