from __future__ import annotations

import unittest

from code_agent.core.completion_contract import TaskIntent
from code_agent.core.limits import (
    BudgetLeaseTier,
    EngineLimits,
    TaskBudget,
    TaskProgressSnapshot,
    lease_limits,
    select_budget_lease,
)
from code_agent.core.task import TaskAuthorization, TaskContract


class BudgetLeaseTests(unittest.TestCase):
    def contract(
        self, objective: str, intent: TaskIntent = TaskIntent.MODIFY
    ) -> TaskContract:
        return TaskContract(
            objective,
            TaskAuthorization.local_workspace("C:/repo"),
            intent=intent,
        )

    def test_initial_tier_uses_intent_and_explicit_depth_signal(self) -> None:
        self.assertIs(
            select_budget_lease(self.contract("explain this", TaskIntent.ANALYZE)),
            BudgetLeaseTier.QUICK,
        )
        self.assertIs(
            select_budget_lease(self.contract("fix the parser")),
            BudgetLeaseTier.STANDARD,
        )
        for objective in (
            "深度调查这个问题",
            "请做跨模块修复",
            "perform an exhaustive review",
            "repository-wide migration",
        ):
            with self.subTest(objective=objective):
                self.assertIs(
                    select_budget_lease(self.contract(objective, TaskIntent.ANALYZE)),
                    BudgetLeaseTier.DEEP,
                )

    def test_lease_limits_are_clipped_by_hard_limits(self) -> None:
        limits = EngineLimits(max_agent_rounds=3, max_tool_calls=5)

        self.assertEqual(lease_limits(BudgetLeaseTier.QUICK, limits), (3, 5))
        budget = TaskBudget("model", limits, lease_tier=BudgetLeaseTier.DEEP)
        self.assertEqual(
            (budget.lease_model_turn_limit, budget.lease_tool_call_limit),
            (3, 5),
        )

    def test_progress_digest_changes_only_with_host_facts(self) -> None:
        initial = TaskProgressSnapshot()
        repeated = TaskProgressSnapshot()
        read = TaskProgressSnapshot(
            action_fingerprint="read:a", reason="new read result"
        )
        changed = TaskProgressSnapshot(
            code_generation=1,
            subject_hash="a" * 64,
            reason="new code generation",
        )

        self.assertEqual(initial.digest, repeated.digest)
        self.assertNotEqual(initial.digest, read.digest)
        self.assertNotEqual(read.digest, changed.digest)
        self.assertEqual(len(changed.digest), 64)

    def test_old_task_budget_construction_remains_valid(self) -> None:
        budget = TaskBudget("model", EngineLimits(), 2, 3, 5, 7)

        self.assertEqual((budget.model_turns, budget.tool_calls), (2, 3))
        self.assertIs(budget.lease_tier, BudgetLeaseTier.STANDARD)
        self.assertEqual(
            (budget.lease_model_turn_limit, budget.lease_tool_call_limit),
            (12, 30),
        )

    def test_invalid_persisted_lease_values_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "hard task limit"):
            TaskBudget(
                "model",
                EngineLimits(max_agent_rounds=4),
                lease_model_turn_limit=5,
            )
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            TaskBudget(
                "model", EngineLimits(), lease_progress_baseline="not-a-digest"
            )


if __name__ == "__main__":
    unittest.main()
