from __future__ import annotations

import hashlib
import unittest

from code_agent.core.cancellation import CancellationToken
from code_agent.core.limits import EngineLimits
from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.orchestration.models import AgentMode
from code_agent.plugins.events import DeclarativeEventRouter, EventProjection
from code_agent.plugins.models import (
    ActionProposal,
    EventSubscription,
    PluginContributions,
    PluginManifest,
    PluginRisk,
    UiPrimitive,
    UiRequest,
)
from code_agent.plugins.registry import PluginHost, PluginRegistryBuilder
from code_agent.plugins.runtime import PluginEventRuntime


DIGEST = hashlib.sha256(b"runtime").hexdigest()


class InteractionHost:
    def __init__(self) -> None:
        self.notifications: list[object] = []
        self.interactions: list[object] = []

    async def notify(self, proposal: object) -> object:
        self.notifications.append(proposal)
        return "shown"

    async def interact(
        self, proposal: object, cancellation: CancellationToken
    ) -> object:
        self.interactions.append(proposal)
        return "accepted"


class ActionExecutor:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def execute(
        self, proposal: object, cancellation: CancellationToken
    ) -> object:
        self.calls.append(proposal)
        return "executed"


def _host() -> PluginHost:
    modes = ModeRegistry(
        standard_mode_definitions(
            {mode: mode.value for mode in AgentMode},
            tools_by_mode={mode: ("read_file",) for mode in AgentMode},
            limits_by_mode={mode: EngineLimits() for mode in AgentMode},
        )
    )
    manifest = PluginManifest(
        "runtime-plugin",
        "runtime",
        "1.0.0",
        "1",
        DIGEST,
        "plugin.json",
        True,
        True,
        PluginContributions(
            events=(
                EventSubscription(
                    "notify",
                    ("task_completed",),
                    ui=UiRequest(UiPrimitive.NOTIFY, "Done", "Task completed"),
                ),
                EventSubscription(
                    "confirm",
                    ("task_completed",),
                    ui=UiRequest(UiPrimitive.CONFIRM, "Confirm", "Continue?"),
                ),
                EventSubscription(
                    "inspect",
                    ("task_completed",),
                    action=ActionProposal(
                        "read_file", {"path": "README.md"}, PluginRisk.READ
                    ),
                ),
            )
        ),
    )
    snapshot = PluginRegistryBuilder(
        modes,
        host_actions=("read_file",),
        host_action_risks={"read_file": PluginRisk.READ},
    ).build((manifest,))
    host = PluginHost()
    host.stage(snapshot)
    host.apply(task_active=False)
    return host


class PluginEventRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_ui_and_action_proposals_follow_host_owned_paths(self) -> None:
        host = _host()
        interactions = InteractionHost()
        actions = ActionExecutor()
        runtime = PluginEventRuntime(
            host, DeclarativeEventRouter(host), interactions, actions
        )

        outcomes = await runtime.handle(
            EventProjection("task_completed", "task-1"), CancellationToken()
        )

        self.assertEqual(len(interactions.notifications), 1)
        self.assertEqual(len(interactions.interactions), 1)
        self.assertEqual(len(actions.calls), 1)
        self.assertTrue(all(outcome.ok for outcome in outcomes))

    async def test_stale_proposal_fails_closed_after_revocation(self) -> None:
        host = _host()
        router = DeclarativeEventRouter(host)
        proposal = router.route(EventProjection("task_completed", "task-1"))[0]
        runtime = PluginEventRuntime(
            host, router, InteractionHost(), ActionExecutor()
        )
        host.revoke("runtime-plugin")

        outcome = await runtime.execute(proposal, CancellationToken())

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.code, "plugin_inactive")


if __name__ == "__main__":
    unittest.main()
