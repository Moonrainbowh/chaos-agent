from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.policy.classifier import classify_action  # noqa: E402
from code_agent.policy.models import (  # noqa: E402
    ApprovalMode,
    Capability,
    DecisionOutcome,
    PolicyDecision,
    RiskLevel,
)


def request(name: str, **arguments: object) -> ActionRequest:
    return ActionRequest(id="action-1", name=name, arguments=arguments)


class PolicyModelTests(unittest.TestCase):
    def test_enums_have_stable_wire_values(self) -> None:
        self.assertEqual(
            [item.value for item in ApprovalMode],
            ["unrestricted", "plan", "ask", "auto", "elevated", "full-local"],
        )
        self.assertEqual(
            [item.value for item in Capability],
            ["read", "write", "execute", "network", "outside_workspace", "raw_shell", "raw_process", "verification", "protected_path", "explicit_approval"],
        )
        self.assertEqual(
            [item.value for item in DecisionOutcome], ["allow", "ask", "deny"]
        )
        self.assertEqual(
            [item.value for item in RiskLevel],
            ["low", "medium", "high", "critical"],
        )

    def test_policy_decision_is_immutable_and_freezes_capabilities(self) -> None:
        source = {Capability.READ}
        decision = PolicyDecision(
            outcome=DecisionOutcome.ALLOW,
            risk=RiskLevel.LOW,
            reason="read-only action",
            capabilities=source,
        )

        source.add(Capability.NETWORK)

        self.assertEqual(decision.capabilities, frozenset({Capability.READ}))
        with self.assertRaises(FrozenInstanceError):
            decision.reason = "changed"  # type: ignore[misc]


class ActionClassifierTests(unittest.TestCase):
    def test_batch_code_slices_is_a_low_risk_read(self) -> None:
        classification = classify_action(request(
            "read_code_slices",
            generation=1,
            targets=[{"path": "src/app.py"}],
        ))

        self.assertTrue(classification.known_tool)
        self.assertEqual(classification.risk, RiskLevel.LOW)
        self.assertIn(Capability.READ, classification.capabilities)

    def test_known_read_and_write_tools_receive_expected_capabilities(self) -> None:
        for name in ("read_file", "list_files", "search_text", "git_status", "git_diff"):
            with self.subTest(name=name):
                classified = classify_action(request(name, path="src/example.py"))
                self.assertTrue(classified.known_tool)
                self.assertEqual(classified.capabilities, frozenset({Capability.READ}))
                self.assertEqual(classified.risk, RiskLevel.LOW)

        for name in (
            "write_file",
            "replace_text",
            "create_checkpoint",
            "restore_checkpoint",
        ):
            with self.subTest(name=name):
                classified = classify_action(request(name, path="src/example.py"))
                self.assertTrue(classified.known_tool)
                self.assertEqual(classified.capabilities, frozenset({Capability.WRITE}))
                self.assertEqual(classified.risk, RiskLevel.MEDIUM)

    def test_run_command_detects_network_signals_case_insensitively(self) -> None:
        commands = (
            "curl https://example.test",
            "IWR https://example.test",
            "  InVoKe-WeBrEqUeSt   https://example.test ",
            "python -m pip\tinstall requests",
            "uv   add httpx",
            "UV\tSYNC",
        )

        for command in commands:
            with self.subTest(command=command):
                classified = classify_action(request("run_command", command=command))
                self.assertEqual(
                    classified.capabilities,
                    frozenset({Capability.EXECUTE, Capability.NETWORK, Capability.RAW_SHELL}),
                )
                self.assertEqual(classified.risk, RiskLevel.HIGH)

    def test_run_command_detects_destructive_and_elevated_signals(self) -> None:
        commands = (
            "git reset --hard HEAD~1",
            "git clean -fd",
            "rm -rf build",
            "Remove-Item   build   -FORCE\t-ReCurse",
            "del /s /q build\\*",
            "rmdir /S /Q build",
            "format C:",
            "diskpart /s layout.txt",
            "shutdown /s /t 0",
            "Stop-Computer -Force",
            "powershell -Verb RunAs",
            "sudo apt update",
            "runas /user:Administrator cmd",
            "Set-ExecutionPolicy RemoteSigned",
        )

        for command in commands:
            with self.subTest(command=command):
                classified = classify_action(request("run_command", command=command))
                self.assertEqual(classified.risk, RiskLevel.CRITICAL)
                self.assertIn(Capability.EXECUTE, classified.capabilities)

    def test_remove_item_is_not_critical_without_both_required_flags(self) -> None:
        for command in ("Remove-Item build -Recurse", "Remove-Item build -Force"):
            with self.subTest(command=command):
                classified = classify_action(request("run_command", command=command))
                self.assertNotEqual(classified.risk, RiskLevel.CRITICAL)

    def test_explicit_outside_workspace_access_is_high_risk(self) -> None:
        classified = classify_action(
            request("write_file", path="../other/file.txt", outside_workspace=True)
        )

        self.assertEqual(
            classified.capabilities,
            frozenset({Capability.WRITE, Capability.OUTSIDE_WORKSPACE}),
        )
        self.assertEqual(classified.risk, RiskLevel.HIGH)

    def test_unknown_tool_and_malformed_command_fail_closed(self) -> None:
        unknown = classify_action(request("delete_everything"))
        malformed = classify_action(request("run_command", command=["echo", "hello"]))

        self.assertFalse(unknown.known_tool)
        self.assertEqual(unknown.risk, RiskLevel.CRITICAL)
        self.assertEqual(malformed.risk, RiskLevel.CRITICAL)

    def test_raw_shell_verification_and_protected_paths_have_distinct_capabilities(self) -> None:
        raw = classify_action(request("run_command", command="python -m unittest"))
        verification = classify_action(request("run_verification", kind="pytest", cwd=".", targets=[]))
        protected = classify_action(request("read_file", path=".env"))
        managed_storage = classify_action(
            request(
                "read_file",
                path="chaos-agent-workspaces/worktrees/another-task/file.py",
            )
        )

        self.assertIn(Capability.RAW_SHELL, raw.capabilities)
        self.assertIn(Capability.VERIFICATION, verification.capabilities)
        self.assertIn(Capability.PROTECTED_PATH, protected.capabilities)
        self.assertIn(Capability.PROTECTED_PATH, managed_storage.capabilities)


if __name__ == "__main__":
    unittest.main()
