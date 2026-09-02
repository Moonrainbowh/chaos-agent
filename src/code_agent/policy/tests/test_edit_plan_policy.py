from __future__ import annotations

import unittest
from pathlib import Path

from code_agent.core.models import ActionRequest
from code_agent.core.task import TaskAuthorization
from code_agent.policy.classifier import (
    classify_action,
    requires_explicit_edit_plan_approval,
)
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.policy.models import ApprovalMode, Capability, DecisionOutcome


def request(name: str) -> ActionRequest:
    return ActionRequest("request", name, {})


class EditPlanPolicyTests(unittest.TestCase):
    def test_plan_is_read_and_apply_is_write(self) -> None:
        planned = classify_action(request("plan_workspace_edits_v1"))
        applied = classify_action(request("apply_workspace_edit_plan_v1"))

        self.assertEqual(planned.capabilities, frozenset({Capability.READ}))
        self.assertEqual(applied.capabilities, frozenset({Capability.WRITE}))

    def test_only_destructive_or_dirty_flags_require_explicit_approval(self) -> None:
        for flag in (
            "dirty",
            "dirty_baseline",
            "non_git_existing",
            "untracked_existing",
            "delete",
            "move",
            "case_only_move",
        ):
            with self.subTest(flag=flag):
                self.assertTrue(requires_explicit_edit_plan_approval((flag,)))
        self.assertFalse(requires_explicit_edit_plan_approval(()))

    def test_workspace_task_grant_trusts_local_edit_risk(self) -> None:
        root = Path("C:/repo")
        grant = TaskAuthorization.local_workspace(str(root))
        decision = ActionPolicy(
            PolicyConfig(ApprovalMode.AUTO, workspace_root=root)
        ).evaluate(
            request("apply_workspace_edit_plan_v1"),
            grant,
            trusted_edit_risk_flags=("move",),
        )
        self.assertEqual(decision.outcome, DecisionOutcome.ALLOW)
        self.assertNotIn(Capability.EXPLICIT_APPROVAL, decision.capabilities)

    def test_plan_mode_still_denies_apply_with_explicit_risk(self) -> None:
        decision = ActionPolicy(PolicyConfig(ApprovalMode.PLAN)).evaluate(
            request("apply_workspace_edit_plan_v1"),
            trusted_edit_risk_flags=("delete",),
        )
        self.assertEqual(decision.outcome, DecisionOutcome.DENY)


if __name__ == "__main__":
    unittest.main()
