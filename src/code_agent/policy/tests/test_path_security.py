from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath


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


def request(name: str, **arguments: object) -> ActionRequest:
    return ActionRequest(id="path-action", name=name, arguments=arguments)


class PathSecurityTests(unittest.TestCase):
    def test_access_levels_apply_distinct_external_file_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            workspace.mkdir()
            outside = str(base / "outside.txt")
            read = request("read_file", path=outside)
            write = request("write_file", path=outside, content="x")

            plan = ActionPolicy(
                PolicyConfig(ApprovalMode.PLAN, workspace_root=workspace)
            )
            ask = ActionPolicy(
                PolicyConfig(ApprovalMode.ASK, workspace_root=workspace)
            )
            elevated = ActionPolicy(
                PolicyConfig(ApprovalMode.ELEVATED, workspace_root=workspace)
            )
            full = ActionPolicy(
                PolicyConfig(ApprovalMode.FULL_LOCAL, workspace_root=workspace)
            )

        self.assertEqual(plan.evaluate(read).outcome, DecisionOutcome.DENY)
        self.assertEqual(ask.evaluate(read).outcome, DecisionOutcome.ASK)
        self.assertEqual(ask.evaluate(write).outcome, DecisionOutcome.DENY)
        self.assertEqual(elevated.evaluate(read).outcome, DecisionOutcome.ASK)
        self.assertEqual(elevated.evaluate(write).outcome, DecisionOutcome.ASK)
        self.assertEqual(full.evaluate(read).outcome, DecisionOutcome.ALLOW)
        self.assertEqual(full.evaluate(write).outcome, DecisionOutcome.ALLOW)

    def test_absolute_path_outside_workspace_is_high_risk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            workspace.mkdir()
            outside = base / "outside.txt"
            outside.write_text("secret", encoding="utf-8")

            classified = classify_action(
                request("read_file", path=str(outside)), workspace_root=workspace
            )

        self.assertEqual(classified.risk, RiskLevel.HIGH)
        self.assertEqual(
            classified.capabilities,
            frozenset({Capability.READ, Capability.OUTSIDE_WORKSPACE}),
        )

    def test_parent_relative_write_path_is_high_risk_without_self_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()

            classified = classify_action(
                request("write_file", path=r"..\secret.txt"),
                workspace_root=workspace,
            )

        self.assertEqual(classified.risk, RiskLevel.HIGH)
        self.assertIn(Capability.OUTSIDE_WORKSPACE, classified.capabilities)

    def test_absolute_path_inside_workspace_is_not_misclassified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            inside = workspace / "src" / "inside.py"
            inside.parent.mkdir(parents=True)
            inside.write_text("pass\n", encoding="utf-8")

            classified = classify_action(
                request("read_file", file=str(inside)), workspace_root=workspace
            )

        self.assertEqual(classified.risk, RiskLevel.LOW)
        self.assertEqual(classified.capabilities, frozenset({Capability.READ}))

    def test_nested_path_arguments_are_checked_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            workspace.mkdir()

            classified = classify_action(
                request(
                    "write_file",
                    options={"destination": {"directory": str(base / "other")}},
                ),
                workspace_root=workspace,
            )

        self.assertEqual(classified.risk, RiskLevel.HIGH)
        self.assertIn(Capability.OUTSIDE_WORKSPACE, classified.capabilities)

    def test_without_root_windows_absolute_or_parent_paths_are_conservative(self) -> None:
        paths = (str(PureWindowsPath("C:/outside/file.txt")), "../secret.txt")

        for path in paths:
            with self.subTest(path=path):
                classified = classify_action(request("read_file", path=path))
                self.assertEqual(classified.risk, RiskLevel.HIGH)
                self.assertIn(
                    Capability.OUTSIDE_WORKSPACE, classified.capabilities
                )

    def test_action_policy_passes_workspace_root_to_classifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            workspace.mkdir()
            policy = ActionPolicy(
                PolicyConfig(
                    approval_mode=ApprovalMode.AUTO, workspace_root=workspace
                )
            )

            outside = policy.evaluate(
                request("read_file", path=str(base / "outside.txt"))
            )
            inside = policy.evaluate(
                request("read_file", path=str(workspace / "inside.txt"))
            )

        self.assertEqual(outside.outcome, DecisionOutcome.ASK)
        self.assertEqual(outside.risk, RiskLevel.HIGH)
        self.assertEqual(inside.outcome, DecisionOutcome.ALLOW)


if __name__ == "__main__":
    unittest.main()
