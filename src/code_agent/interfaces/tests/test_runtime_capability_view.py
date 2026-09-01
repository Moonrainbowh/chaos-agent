from __future__ import annotations

import unittest

from code_agent.interfaces.capability_view import ModePermissionView, PermissionSummary
from code_agent.orchestration.models import AgentMode
from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.policy.models import ApprovalMode
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig


class RuntimeCapabilityViewTests(unittest.TestCase):
    def test_effective_effort_and_single_delegation_are_displayed(self) -> None:
        profile = ModelProfile(
            "sol",
            ProviderConfig(
                "https://api.example.test",
                "gpt-sol",
                ApiProtocol.RESPONSES,
                "TEST_KEY",
            ),
            8_000,
            1_000,
        )
        registry = ModeRegistry(
            standard_mode_definitions(
                {mode: "sol" for mode in AgentMode},
                tools_by_mode={
                    mode: ("read_file", "delegate_agent") for mode in AgentMode
                },
            )
        )
        snapshot = registry.freeze_runtime(
            "medium",
            {"sol": profile},
            profile_id="sol",
            topology="single",
            reasoning_effort="max",
        )
        view = ModePermissionView(
            snapshot,
            PermissionSummary(ApprovalMode.AUTO, "G:\\repo", False, True),
        )

        lines = view.lines()

        self.assertIn("topology single", lines[0])
        self.assertIn("reasoning: max · tools 1", lines[1])


if __name__ == "__main__":
    unittest.main()
