from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.core.task import TaskAuthorization  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import (  # noqa: E402
    ApprovalMode,
    Capability,
    DecisionOutcome,
    RiskLevel,
)


def request(name: str, **arguments: object) -> ActionRequest:
    return ActionRequest(id="action-1", name=name, arguments=arguments)


class ActionPolicyModeTests(unittest.TestCase):
    def policy(
        self, mode: ApprovalMode, *, allow_network: bool = False
    ) -> ActionPolicy:
        return ActionPolicy(
            PolicyConfig(approval_mode=mode, allow_network=allow_network)
        )

    def test_unrestricted_mode_allows_noncritical_commands_without_approval(self) -> None:
        policy = self.policy(ApprovalMode.UNRESTRICTED)

        for action in (
            request("run_command", command="python -m unittest"),
            request("run_command", command="pip install requests"),
            request("write_file", path="../outside.py", content="x"),
            request("read_file", path=".env"),
        ):
            with self.subTest(action=action.name, arguments=action.arguments):
                self.assertEqual(
                    policy.evaluate(action).outcome,
                    DecisionOutcome.ALLOW,
                )

    def test_policy_config_defaults_to_unrestricted(self) -> None:
        self.assertIs(PolicyConfig().approval_mode, ApprovalMode.UNRESTRICTED)

    def test_plan_mode_only_allows_read_only_actions(self) -> None:
        policy = self.policy(ApprovalMode.PLAN)

        self.assertEqual(
            policy.evaluate(request("read_file", path="README.md")).outcome,
            DecisionOutcome.ALLOW,
        )
        self.assertEqual(
            policy.evaluate(request("write_file", path="README.md")).outcome,
            DecisionOutcome.DENY,
        )
        self.assertEqual(
            policy.evaluate(request("run_command", command="python --version")).outcome,
            DecisionOutcome.DENY,
        )

    def test_ask_mode_allows_reads_asks_for_other_known_actions(self) -> None:
        policy = self.policy(ApprovalMode.ASK)

        self.assertEqual(
            policy.evaluate(request("git_diff")).outcome, DecisionOutcome.ALLOW
        )
        self.assertEqual(
            policy.evaluate(request("replace_text", path="a.py")).outcome,
            DecisionOutcome.ASK,
        )
        self.assertEqual(
            policy.evaluate(request("run_command", command="python -V")).outcome,
            DecisionOutcome.ASK,
        )

    def test_auto_mode_allows_typed_reads_and_workspace_writes(self) -> None:
        policy = self.policy(ApprovalMode.AUTO)

        cases = (
            request("list_files", path="src"),
            request("write_file", path="src/example.py"),
        )
        for action in cases:
            with self.subTest(name=action.name, arguments=action.arguments):
                self.assertEqual(
                    policy.evaluate(action).outcome, DecisionOutcome.ALLOW
                )

    def test_auto_mode_asks_for_every_noncritical_execute_action(self) -> None:
        policy = self.policy(ApprovalMode.AUTO)

        decision = policy.evaluate(
            request("run_command", command="python -m unittest")
        )

        self.assertEqual(decision.outcome, DecisionOutcome.ASK)
        self.assertIn(Capability.EXECUTE, decision.capabilities)

    def test_auto_mode_asks_for_high_risk_network_commands(self) -> None:
        action = request("run_command", command="PIP   INSTALL requests")

        blocked = self.policy(ApprovalMode.AUTO).evaluate(action)
        allowed = self.policy(ApprovalMode.AUTO, allow_network=True).evaluate(action)

        self.assertEqual(blocked.outcome, DecisionOutcome.ASK)
        self.assertIn(Capability.NETWORK, blocked.capabilities)
        self.assertEqual(allowed.outcome, DecisionOutcome.ASK)

    def test_auto_mode_asks_for_outside_workspace_or_high_risk(self) -> None:
        decision = self.policy(ApprovalMode.AUTO).evaluate(
            request("read_file", path="../secret.txt", outside_workspace=True)
        )

        self.assertEqual(decision.outcome, DecisionOutcome.ASK)
        self.assertEqual(decision.risk, RiskLevel.HIGH)
        self.assertIn(Capability.OUTSIDE_WORKSPACE, decision.capabilities)

    def test_critical_and_unknown_actions_are_denied_in_every_mode(self) -> None:
        actions = (
            request("run_command", command="Remove-Item x -Recurse -Force"),
            request("unregistered_tool"),
        )

        for mode in ApprovalMode:
            for action in actions:
                with self.subTest(mode=mode, name=action.name):
                    decision = self.policy(mode, allow_network=True).evaluate(action)
                    self.assertEqual(decision.outcome, DecisionOutcome.DENY)
                    self.assertEqual(decision.risk, RiskLevel.CRITICAL)

    def test_repeated_evaluation_has_a_stable_explanation(self) -> None:
        policy = self.policy(ApprovalMode.AUTO)
        action = request("run_command", command="curl https://example.test")

        first = policy.evaluate(action)
        second = policy.evaluate(action)

        self.assertEqual(first, second)
        self.assertTrue(first.reason.strip())

    def test_task_grant_cannot_bypass_network_outside_or_critical_boundaries(self) -> None:
        policy = ActionPolicy(
            PolicyConfig(ApprovalMode.AUTO, workspace_root=Path("C:/repo"))
        )
        grant = TaskAuthorization.local_workspace("C:/repo")
        cases = (
            request("run_command", command="pip install package"),
            request("write_file", path="../outside.py", content="x"),
            request("run_command", command="Remove-Item temp -Recurse -Force"),
            request("unregistered_tool"),
        )

        for action in cases:
            with self.subTest(action=action.name, arguments=action.arguments):
                self.assertNotEqual(
                    policy.evaluate(action, grant).outcome,
                    DecisionOutcome.ALLOW,
                )

    def test_task_grant_requires_approval_for_raw_shell_and_protected_paths(self) -> None:
        policy = ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=Path("C:/repo")))
        grant = TaskAuthorization.local_workspace("C:/repo")

        for action in (request("run_command", command="Get-Content C:/Users/lack/.ssh/id_rsa"), request("read_file", path=".env")):
            with self.subTest(action=action.name):
                self.assertEqual(policy.evaluate(action, grant).outcome, DecisionOutcome.ASK)

    def test_task_grant_allows_only_structured_local_verification(self) -> None:
        policy = ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=Path("C:/repo")))
        grant = TaskAuthorization.local_workspace("C:/repo")

        decision = policy.evaluate(request("run_verification", kind="pytest", cwd=".", targets=[]), grant)

        self.assertEqual(decision.outcome, DecisionOutcome.ALLOW)


class PolicyConfigTests(unittest.TestCase):
    def test_configuration_is_immutable(self) -> None:
        config = PolicyConfig(ApprovalMode.AUTO, allow_network=True)

        with self.assertRaises(FrozenInstanceError):
            config.allow_network = False  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
