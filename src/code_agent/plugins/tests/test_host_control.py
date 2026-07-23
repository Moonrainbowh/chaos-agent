from __future__ import annotations

import hashlib
import unittest

from code_agent.core.limits import EngineLimits
from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.orchestration.models import AgentMode
from code_agent.plugins.commands import PluginCommandCatalog
from code_agent.plugins.events import DeclarativeEventRouter, EventProjection
from code_agent.plugins.models import (
    CommandContribution,
    EventSubscription,
    PluginContributions,
    PluginManifest,
    UiPrimitive,
    UiRequest,
)
from code_agent.plugins.registry import PluginHost, PluginRegistryBuilder
from code_agent.plugins.status import PluginReloadState


def _modes() -> ModeRegistry:
    return ModeRegistry(
        standard_mode_definitions(
            {mode: mode.value for mode in AgentMode},
            tools_by_mode={mode: ("read_file",) for mode in AgentMode},
            limits_by_mode={mode: EngineLimits() for mode in AgentMode},
        )
    )


def _manifest(
    *,
    trusted: bool = True,
    enabled: bool = True,
    revision: str = "one",
) -> PluginManifest:
    return PluginManifest(
        "control-plugin",
        "control",
        "1.0.0",
        "1",
        hashlib.sha256(revision.encode("utf-8")).hexdigest(),
        "plugin.json",
        trusted,
        enabled,
        PluginContributions(
            commands=(CommandContribution("inspect", "Inspect", "review"),),
            events=(
                EventSubscription(
                    "notice",
                    ("task_completed",),
                    ui=UiRequest(UiPrimitive.NOTIFY, "Done", "Task completed"),
                ),
            ),
        ),
    )


def _snapshot(
    *,
    trusted: bool = True,
    enabled: bool = True,
    revision: str = "one",
):
    return PluginRegistryBuilder(
        _modes(), controllers=("review",)
    ).build(
        (_manifest(trusted=trusted, enabled=enabled, revision=revision),)
    )


class PluginHostControlTests(unittest.TestCase):
    def test_status_and_list_publish_current_and_staged_reload_state(self) -> None:
        host = PluginHost(_snapshot())

        self.assertEqual(host.list(), host.status())
        self.assertEqual(host.status("control-plugin")[0].enabled, True)
        self.assertEqual(host.state().reload_state, PluginReloadState.IDLE)
        self.assertEqual(host.state().staged_plugins, ())

        host.stage(_snapshot(revision="two"))
        state = host.state()

        self.assertEqual(state.reload_state, PluginReloadState.STAGED)
        self.assertEqual(state.generation, 0)
        self.assertNotEqual(
            state.plugins[0].digest, state.staged_plugins[0].digest
        )

    def test_active_task_keeps_reload_staged_until_idle_apply(self) -> None:
        host = PluginHost(_snapshot())
        invocation = PluginCommandCatalog(host).resolve("control.inspect")
        proposal = DeclarativeEventRouter(host).route(
            EventProjection("task_completed", "task-1")
        )[0]
        host.stage(_snapshot(revision="two"))

        self.assertFalse(host.apply(task_active=True))
        self.assertEqual(host.generation, 0)
        self.assertEqual(host.reload_state, PluginReloadState.STAGED)
        self.assertTrue(
            host.is_active(
                invocation.plugin_id, invocation.digest, invocation.generation
            )
        )

        self.assertTrue(host.apply(task_active=False))
        self.assertEqual(host.generation, 1)
        self.assertEqual(host.reload_state, PluginReloadState.IDLE)
        self.assertFalse(
            host.is_active(
                invocation.plugin_id, invocation.digest, invocation.generation
            )
        )
        self.assertFalse(
            host.is_active(
                proposal.plugin_id, proposal.plugin_digest, proposal.generation
            )
        )

    def test_disable_and_trusted_enable_each_invalidate_old_calls(self) -> None:
        host = PluginHost(_snapshot())
        catalog = PluginCommandCatalog(host)
        router = DeclarativeEventRouter(host)
        old_invocation = catalog.resolve("control.inspect")
        old_proposal = router.route(
            EventProjection("task_completed", "task-1")
        )[0]

        disabled = host.disable("control-plugin")

        self.assertFalse(disabled.enabled)
        self.assertEqual(host.generation, 1)
        self.assertEqual(host.contributions(), ())
        self.assertFalse(
            host.is_active(
                old_invocation.plugin_id,
                old_invocation.digest,
                old_invocation.generation,
            )
        )

        enabled = host.enable("control-plugin")

        self.assertTrue(enabled.enabled)
        self.assertEqual(host.generation, 2)
        self.assertTrue(host.contributions())
        self.assertFalse(
            host.is_active(
                old_invocation.plugin_id,
                old_invocation.digest,
                old_invocation.generation,
            )
        )
        self.assertFalse(
            host.is_active(
                old_proposal.plugin_id,
                old_proposal.plugin_digest,
                old_proposal.generation,
            )
        )
        current = catalog.resolve("control.inspect")
        self.assertTrue(
            host.is_active(current.plugin_id, current.digest, current.generation)
        )

    def test_enable_requires_a_loaded_trusted_snapshot(self) -> None:
        host = PluginHost(_snapshot(trusted=False))
        host.disable("control-plugin")
        generation = host.generation

        with self.assertRaises(PermissionError):
            host.enable("control-plugin")
        self.assertEqual(host.generation, generation)
        self.assertFalse(host.status("control-plugin")[0].enabled)

        with self.assertRaises(KeyError):
            host.enable("missing")
        with self.assertRaises(KeyError):
            host.disable("missing")
        self.assertFalse(host.revoke("missing"))
        self.assertEqual(host.generation, generation)

    def test_trusted_disk_disabled_plugin_is_listed_and_can_be_enabled(self) -> None:
        host = PluginHost(_snapshot(enabled=False))

        self.assertFalse(host.status("control-plugin")[0].enabled)
        self.assertEqual(host.contributions(), ())

        enabled = host.enable("control-plugin")

        self.assertTrue(enabled.enabled)
        self.assertTrue(host.contributions())

    def test_reload_does_not_reenable_an_explicitly_disabled_plugin(self) -> None:
        host = PluginHost(_snapshot())
        host.revoke("control-plugin")
        self.assertEqual(host.generation, 1)
        host.stage(_snapshot(revision="two"))

        self.assertTrue(host.apply(task_active=False))

        self.assertEqual(host.generation, 2)
        self.assertFalse(host.status("control-plugin")[0].enabled)
        self.assertEqual(host.contributions(), ())
        host.enable("control-plugin")
        self.assertEqual(host.generation, 3)

    def test_successful_repeated_lifecycle_actions_advance_generation(self) -> None:
        host = PluginHost(_snapshot())

        host.disable("control-plugin")
        host.disable("control-plugin")
        host.enable("control-plugin")
        host.enable("control-plugin")

        self.assertEqual(host.generation, 4)


if __name__ == "__main__":
    unittest.main()
