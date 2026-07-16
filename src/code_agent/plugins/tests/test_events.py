from __future__ import annotations

import unittest

from code_agent.core.limits import EngineLimits
from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.orchestration.models import AgentMode
from code_agent.plugins.events import DeclarativeEventRouter, EventProjection
from code_agent.plugins.manifest import PluginTrustStore, manifest_digest, parse_manifest
from code_agent.plugins.registry import PluginHost, PluginRegistryBuilder

from test_manifest import manifest_data


def _host() -> PluginHost:
    modes = ModeRegistry(
        standard_mode_definitions(
            {mode: mode.value for mode in AgentMode},
            tools_by_mode={mode: ("read_file",) for mode in AgentMode},
            limits_by_mode={mode: EngineLimits() for mode in AgentMode},
        )
    )
    raw = manifest_data()
    raw["digest"] = manifest_digest(raw)
    plugin = parse_manifest(raw, "plugin.json", PluginTrustStore(), host_api="1", approved=True)
    snapshot = PluginRegistryBuilder(modes, host_actions=("read_file",)).build((plugin,))
    return PluginHost(snapshot)


class DeclarativeEventRouterTests(unittest.TestCase):
    def test_routes_only_matching_bounded_host_ui_proposals(self) -> None:
        router = DeclarativeEventRouter(_host())

        ignored = router.route(EventProjection("task_started", "task-1"))
        proposals = router.route(EventProjection("task_completed", "task-1", {"status": "completed"}))

        self.assertEqual(ignored, ())
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].ui.primitive.value, "notify")  # type: ignore[union-attr]
        self.assertIsNone(proposals[0].action)
        self.assertEqual(router.route(EventProjection("task_completed", "task-1"), depth=2), ())

    def test_sensitive_event_projection_fields_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            EventProjection("task_completed", "task-1", {"api_key": "secret"})


if __name__ == "__main__":
    unittest.main()
