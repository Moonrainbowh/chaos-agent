from __future__ import annotations

import unittest

from code_agent.core.limits import EngineLimits
from code_agent.orchestration.codec import snapshot_from_payload, snapshot_payload
from code_agent.orchestration.models import (
    AgentDefinition,
    AgentMode,
    AgentRole,
    ReasoningEffort,
)
from code_agent.orchestration.modes import (
    ModeRegistry,
    standard_mode_definitions,
    standard_mode_order,
)
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig


def _profile(name: str, model: str | None = None) -> ModelProfile:
    return ModelProfile(
        name,
        ProviderConfig(
            "https://example.test",
            model or f"model-{name}",
            ApiProtocol.RESPONSES,
            api_key_env="TEST_KEY",
        ),
        200_000,
        8_192,
    )


def _profiles() -> dict[str, ModelProfile]:
    names = ("low", "medium", "high", "ultra", "oracle")
    return {name: _profile(name) for name in names}


class ModeRegistryTests(unittest.TestCase):
    def test_standard_modes_are_ordered_and_have_distinct_policies(self) -> None:
        definitions = standard_mode_definitions(
            {mode: mode.value for mode in AgentMode},
            oracle_profile_ids={mode: "oracle" for mode in AgentMode},
            tools_by_mode={mode: ("read_file", "mcp.docs.search") for mode in AgentMode},
        )

        self.assertEqual(tuple(item.mode for item in definitions), standard_mode_order())
        self.assertEqual(
            tuple(item.reasoning_effort for item in definitions),
            (
                ReasoningEffort.LOW,
                ReasoningEffort.MEDIUM,
                ReasoningEffort.HIGH,
                ReasoningEffort.XHIGH,
            ),
        )
        self.assertEqual(len({item.prompt_policy for item in definitions}), 4)
        self.assertEqual(
            {item.limits for item in definitions},
            {EngineLimits(120, 256, 64, 800_000, 1_000_000)},
        )

    def test_freeze_records_actual_models_and_round_trips(self) -> None:
        registry = ModeRegistry(
            standard_mode_definitions(
                {mode: mode.value for mode in AgentMode},
                oracle_profile_ids={AgentMode.HIGH: "oracle"},
                tools_by_mode={AgentMode.HIGH: ("read_file", "run_verification")},
            )
        )

        snapshot = registry.freeze(AgentMode.HIGH, _profiles())
        restored = snapshot_from_payload(snapshot_payload(snapshot))

        self.assertEqual(snapshot.model, "model-high")
        self.assertEqual(snapshot.oracle_model, "model-oracle")
        self.assertEqual(snapshot, restored)
        self.assertEqual(len(snapshot.digest), 64)

    def test_freeze_is_stable_and_rejects_missing_profiles(self) -> None:
        registry = ModeRegistry(
            standard_mode_definitions({mode: mode.value for mode in AgentMode})
        )
        profiles = _profiles()

        self.assertEqual(
            registry.freeze("medium", profiles).digest,
            registry.freeze("medium", profiles).digest,
        )
        del profiles["ultra"]
        with self.assertRaisesRegex(ValueError, "not configured"):
            registry.freeze("ultra", profiles)

    def test_registry_requires_all_modes(self) -> None:
        definitions = standard_mode_definitions({mode: mode.value for mode in AgentMode})
        with self.assertRaisesRegex(ValueError, "missing"):
            ModeRegistry(definitions[:-1])

    def test_agent_tools_can_only_narrow_mode_tools(self) -> None:
        registry = ModeRegistry(
            standard_mode_definitions(
                {mode: mode.value for mode in AgentMode},
                tools_by_mode={AgentMode.MEDIUM: ("read_file", "write_file")},
                limits_by_mode={AgentMode.MEDIUM: EngineLimits(max_tool_calls=4)},
            )
        )
        snapshot = registry.freeze("medium", _profiles())
        agent = AgentDefinition(
            "reviewer",
            AgentRole.REVIEW,
            snapshot,
            "Review the requested change.",
            ("read_file",),
        )

        self.assertEqual(agent.effective_tools, ("read_file",))
        self.assertTrue(agent.advisory)
        with self.assertRaisesRegex(ValueError, "subset"):
            AgentDefinition(
                "unsafe",
                AgentRole.SUBAGENT,
                snapshot,
                "Do work.",
                ("run_command",),
            )


if __name__ == "__main__":
    unittest.main()
