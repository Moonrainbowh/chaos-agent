from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.policy.classifier import classify_action  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import (  # noqa: E402
    ApprovalMode,
    Capability,
    DecisionOutcome,
    RiskLevel,
)


def command_request(command: str) -> ActionRequest:
    return ActionRequest(
        id="command-action", name="run_command", arguments={"command": command}
    )


class CommandSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.auto = ActionPolicy(PolicyConfig(approval_mode=ApprovalMode.AUTO))

    def test_remove_item_abbreviations_are_critical_and_denied(self) -> None:
        action = command_request(r"Remove-Item C:\data -r -fo")

        classified = classify_action(action)
        decision = self.auto.evaluate(action)

        self.assertEqual(classified.risk, RiskLevel.CRITICAL)
        self.assertEqual(decision.outcome, DecisionOutcome.DENY)

    def test_recursive_force_variants_are_critical_and_denied(self) -> None:
        commands = (
            r"Remove-Item C:\data -r:$true -fo:$true",
            r"rm C:\data -r -fo",
            r"Remove-Item C:\data -recurse -force",
        )

        for command in commands:
            with self.subTest(command=command):
                action = command_request(command)
                self.assertEqual(
                    classify_action(action).risk, RiskLevel.CRITICAL
                )
                self.assertEqual(
                    self.auto.evaluate(action).outcome, DecisionOutcome.DENY
                )

    def test_powershell_encoded_commands_are_critical_and_denied(self) -> None:
        commands = (
            "powershell.exe -EncodedCommand ZQBjAGgAbwAgAHgA",
            "pwsh -enc ZQBjAGgAbwAgAHgA",
        )

        for command in commands:
            with self.subTest(command=command):
                action = command_request(command)
                self.assertEqual(
                    classify_action(action).risk, RiskLevel.CRITICAL
                )
                self.assertEqual(
                    self.auto.evaluate(action).outcome, DecisionOutcome.DENY
                )

    def test_network_aliases_are_high_risk_and_require_approval(self) -> None:
        commands = (
            "irm https://example.test",
            "Invoke-RestMethod https://example.test",
            "wget https://example.test/file",
            "Start-BitsTransfer https://example.test/file out.bin",
            "git clone https://example.test/repo.git",
            "git fetch origin",
            "git pull origin main",
            "npm install package",
            "pnpm add package",
            "yarn add package",
        )

        for command in commands:
            with self.subTest(command=command):
                action = command_request(command)
                classified = classify_action(action)
                self.assertIn(Capability.NETWORK, classified.capabilities)
                self.assertEqual(classified.risk, RiskLevel.HIGH)
                self.assertEqual(
                    self.auto.evaluate(action).outcome, DecisionOutcome.ASK
                )

    def test_all_noncritical_commands_require_approval_in_auto(self) -> None:
        commands = (
            "git status --short",
            "git diff --stat",
            "git log -1",
            "git show HEAD",
            "git rev-parse HEAD",
            "rg -n TODO src",
            "python -m pytest -q",
            "python -m unittest discover -v",
            "pytest -q",
            "ruff check src",
            "mypy src",
            "pyright src",
            "cargo test",
            "cargo check",
            "go test ./...",
            "npm test",
            "npm run lint",
            "npm run build",
        )

        for command in commands:
            with self.subTest(command=command):
                action = command_request(command)
                self.assertEqual(classify_action(action).risk, RiskLevel.HIGH)
                self.assertEqual(
                    self.auto.evaluate(action).outcome, DecisionOutcome.ASK
                )

    def test_safe_looking_commands_with_side_effect_options_are_not_allowed(self) -> None:
        commands = (
            'rg --pre "python -m unittest" needle .',
            r"git diff --output=C:\outside.txt",
        )

        for command in commands:
            with self.subTest(command=command):
                action = command_request(command)
                self.assertIn(
                    Capability.EXECUTE, classify_action(action).capabilities
                )
                self.assertEqual(
                    self.auto.evaluate(action).outcome, DecisionOutcome.ASK
                )

    def test_shell_control_or_opaque_commands_are_high_and_ask(self) -> None:
        commands = (
            "git status; echo finished",
            "git diff | Set-Content diff.txt",
            "python -c \"print('opaque')\"",
            "echo hello",
        )

        for command in commands:
            with self.subTest(command=command):
                action = command_request(command)
                self.assertEqual(classify_action(action).risk, RiskLevel.HIGH)
                self.assertEqual(
                    self.auto.evaluate(action).outcome, DecisionOutcome.ASK
                )


if __name__ == "__main__":
    unittest.main()
