from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.core.models import ActionRequest
from code_agent.core.task import TaskAuthorization
from code_agent.policy.classifier import classify_action
from code_agent.policy._command_risk import process_risk
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.policy.models import (
    ApprovalMode,
    Capability,
    DecisionOutcome,
    RiskLevel,
)


def process(program: str, *args: str, cwd: str = ".") -> ActionRequest:
    return ActionRequest(
        "process", "run_process_v1", {"program": program, "args": list(args), "cwd": cwd}
    )


class StructuredProcessPolicyTests(unittest.TestCase):
    def test_native_absolute_arguments_keep_their_original_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in ("input.py", "input with spaces.py", "input;draft.py"):
                target = str(root / name)
                with self.subTest(target=target):
                    self.assertIn(target, process_risk("python", (target,)).paths)

    def test_absolute_argument_inside_workspace_remains_authorized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            policy = ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=root))
            action = process("python", str(root / "input with spaces.py"))
            decision = policy.evaluate(action, TaskAuthorization.local_workspace(str(root)))
            self.assertNotIn(Capability.OUTSIDE_WORKSPACE, decision.capabilities)
            self.assertEqual(decision.outcome, DecisionOutcome.ALLOW)

    def test_plain_process_is_raw_high_risk_and_requires_approval(self) -> None:
        action = process("python.exe", "-m", "unittest")
        classified = classify_action(action)
        decision = ActionPolicy(PolicyConfig(ApprovalMode.AUTO)).evaluate(action)

        self.assertEqual(classified.risk, RiskLevel.HIGH)
        self.assertEqual(
            classified.capabilities,
            frozenset({Capability.EXECUTE, Capability.RAW_PROCESS}),
        )
        self.assertEqual(decision.outcome, DecisionOutcome.ASK)

    def test_task_authorization_allows_process_in_trusted_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            action = process("python.exe", "-V")
            policy = ActionPolicy(
                PolicyConfig(ApprovalMode.AUTO, workspace_root=root)
            )

            decision = policy.evaluate(
                action, TaskAuthorization.local_workspace(str(root))
            )

        self.assertEqual(decision.outcome, DecisionOutcome.ALLOW)
        self.assertIn(Capability.RAW_PROCESS, decision.capabilities)

    def test_plan_denies_process_and_unrestricted_allows_noncritical(self) -> None:
        action = process("python.exe", "-V")

        plan = ActionPolicy(PolicyConfig(ApprovalMode.PLAN)).evaluate(action)
        unrestricted = ActionPolicy(
            PolicyConfig(ApprovalMode.UNRESTRICTED)
        ).evaluate(action)

        self.assertEqual(plan.outcome, DecisionOutcome.DENY)
        self.assertEqual(unrestricted.outcome, DecisionOutcome.ALLOW)

    def test_shell_launchers_and_critical_operations_are_always_denied(self) -> None:
        actions = (
            process("pwsh.exe", "-Command", "Get-Date"),
            process("build.cmd", "/q"),
            process("git.exe", "reset", "--hard", "HEAD"),
            process("shutdown.exe", "/s"),
        )
        policy = ActionPolicy(PolicyConfig(ApprovalMode.UNRESTRICTED))

        for action in actions:
            with self.subTest(program=action.arguments["program"]):
                classified = classify_action(action)
                self.assertEqual(classified.risk, RiskLevel.CRITICAL)
                self.assertEqual(
                    policy.evaluate(action).outcome, DecisionOutcome.DENY
                )

    def test_network_programs_are_high_risk_and_keep_network_capability(self) -> None:
        actions = (
            process("curl.exe", "https://example.test"),
            process("git.exe", "clone", "https://example.test/repo.git"),
            process("pip.exe", "install", "package"),
        )
        policy = ActionPolicy(PolicyConfig(ApprovalMode.AUTO))

        for action in actions:
            with self.subTest(program=action.arguments["program"]):
                classified = classify_action(action)
                self.assertEqual(classified.risk, RiskLevel.HIGH)
                self.assertIn(Capability.NETWORK, classified.capabilities)
                self.assertEqual(
                    policy.evaluate(action).outcome, DecisionOutcome.ASK
                )

    def test_outside_cwd_is_never_silently_authorized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            action = process("python.exe", "-V", cwd="..")
            classified = classify_action(action, root)
            policy = ActionPolicy(
                PolicyConfig(ApprovalMode.AUTO, workspace_root=root)
            )

            decision = policy.evaluate(
                action, TaskAuthorization.local_workspace(str(root))
            )

        self.assertIn(Capability.OUTSIDE_WORKSPACE, classified.capabilities)
        self.assertEqual(decision.outcome, DecisionOutcome.ASK)

    def test_outside_or_protected_process_argument_requires_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            policy = ActionPolicy(
                PolicyConfig(ApprovalMode.AUTO, workspace_root=root)
            )
            authorization = TaskAuthorization.local_workspace(str(root))
            cases = (
                (process("python.exe", str(root.parent / "input.py")), Capability.OUTSIDE_WORKSPACE),
                (process("python.exe", ".git/config"), Capability.PROTECTED_PATH),
            )

            for action, capability in cases:
                with self.subTest(argument=action.arguments["args"]):
                    classified = classify_action(action, root)
                    decision = policy.evaluate(action, authorization)

                    self.assertIn(capability, classified.capabilities)
                    self.assertEqual(decision.outcome, DecisionOutcome.ASK)


if __name__ == "__main__":
    unittest.main()
