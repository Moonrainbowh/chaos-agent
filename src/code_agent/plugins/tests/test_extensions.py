from __future__ import annotations

import hashlib
import unittest

from code_agent.core.limits import EngineLimits
from code_agent.interfaces.command_registry import REGISTRY
from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.orchestration.models import AgentMode
from code_agent.orchestration.plugin_extensions import (
    DelegationSelector,
    PluginAgentCatalog,
    PluginModeCatalog,
)
from code_agent.plugins.commands import PluginCommandCatalog
from code_agent.plugins.models import (
    AgentContribution,
    CommandContribution,
    ModeContribution,
    PluginContributions,
    PluginManifest,
)
from code_agent.plugins.registry import (
    ContributionSnapshot,
    PluginHost,
    PluginRegistryBuilder,
)
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig


DIGEST = hashlib.sha256(b"plugin").hexdigest()


def _modes() -> ModeRegistry:
    return ModeRegistry(
        standard_mode_definitions(
            {mode: mode.value for mode in AgentMode},
            tools_by_mode={mode: ("read_file", "write_file") for mode in AgentMode},
            limits_by_mode={mode: EngineLimits() for mode in AgentMode},
        )
    )


def _profiles() -> dict[str, ModelProfile]:
    provider = ProviderConfig(
        "https://api.example.test",
        "model",
        ApiProtocol.RESPONSES,
        api_key_env="TEST_API_KEY",
    )
    return {
        mode.value: ModelProfile(mode.value, provider, 200_000, 8_192)
        for mode in AgentMode
    }


def _manifest() -> PluginManifest:
    return PluginManifest(
        "review-helper",
        "reviewer",
        "1.0.0",
        "1",
        DIGEST,
        "plugin.json",
        True,
        True,
        PluginContributions(
            commands=(CommandContribution("inspect", "Inspect state", "review"),),
            modes=(
                ModeContribution(
                    "strict",
                    AgentMode.MEDIUM,
                    "review",
                    ("read_file",),
                ),
            ),
            agents=(
                AgentContribution(
                    "security",
                    AgentMode.MEDIUM,
                    "Review security.",
                    ("read_file",),
                ),
            ),
        ),
    )


class PluginExtensionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.modes = _modes()
        builder = PluginRegistryBuilder(
            self.modes, controllers=("review",)
        )
        self.snapshot = builder.build((_manifest(),))
        self.host = PluginHost()
        self.host.stage(self.snapshot)
        self.host.apply(task_active=False)

    def test_command_invocation_is_namespaced_and_bound_to_generation(self) -> None:
        catalog = PluginCommandCatalog(self.host)

        invocation = catalog.resolve("reviewer.inspect", ("target",))

        self.assertEqual(invocation.qualified_id, "reviewer.inspect")
        self.assertEqual(invocation.controller, "review")
        self.assertTrue(
            self.host.is_active(
                invocation.plugin_id, invocation.digest, invocation.generation
            )
        )
        registry = REGISTRY.with_plugin_commands(catalog.list())
        spec, arguments, error = registry.parse(
            "/reviewer.inspect target", {"plugins"}
        )
        self.assertIsNone(error)
        self.assertEqual(spec.name, "reviewer.inspect")
        self.assertEqual(spec.controller, "review")
        self.assertEqual(arguments, ("target",))
        self.host.revoke("review-helper")
        self.assertFalse(
            self.host.is_active(
                invocation.plugin_id, invocation.digest, invocation.generation
            )
        )

    def test_mode_and_agent_keep_namespace_and_only_narrow_base(self) -> None:
        profiles = {
            mode: self.modes.freeze(mode, _profiles()) for mode in AgentMode
        }
        mode = PluginModeCatalog(self.host, profiles).resolve("reviewer.strict")
        agent = PluginAgentCatalog(self.host, profiles).resolve("reviewer.security")

        self.assertEqual(mode.identifier, "reviewer.strict")
        self.assertEqual(mode.tool_names, ("read_file",))
        self.assertEqual(agent.agent_id, "reviewer.security")
        self.assertEqual(agent.effective_tools, ("read_file",))

    def test_delegation_role_and_agent_id_are_mutually_exclusive(self) -> None:
        self.assertEqual(DelegationSelector(role="review").role, "review")
        self.assertEqual(
            DelegationSelector(agent_id="reviewer.security").agent_id,
            "reviewer.security",
        )
        with self.assertRaises(ValueError):
            DelegationSelector()
        with self.assertRaises(ValueError):
            DelegationSelector(role="review", agent_id="reviewer.security")


if __name__ == "__main__":
    unittest.main()
