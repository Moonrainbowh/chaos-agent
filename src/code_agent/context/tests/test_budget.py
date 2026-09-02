from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.budget import PromptAllocation, PromptBudget  # noqa: E402
from code_agent.context.errors import ContextError, PromptBudgetError  # noqa: E402
from code_agent.context.models import ContextConfig  # noqa: E402


class PromptBudgetTests(unittest.TestCase):
    def test_default_allocation_reserves_exact_ceiling_capacity(self) -> None:
        allocation = PromptBudget().allocate(
            system_and_rules_tokens=3_000,
            tool_tokens=1_500,
            task_state_tokens=1_000,
        )

        self.assertEqual(
            allocation,
            PromptAllocation(
                rule_tokens=3_000,
                tool_tokens=1_500,
                task_state_tokens=1_000,
                repo_map_tokens=2_000,
                message_tokens=12_000,
                safety_tokens=500,
            ),
        )
        self.assertEqual(allocation.total_tokens, 20_000)

    def test_allocation_shrinks_map_before_messages(self) -> None:
        budget = PromptBudget(
            max_prompt_tokens=10_000,
            max_rule_tokens=3_000,
            max_tool_tokens=1_500,
            max_task_state_tokens=1_000,
            max_repo_map_tokens=2_000,
            max_message_tokens=12_000,
            min_message_tokens=2_000,
            safety_tokens=500,
        )

        allocation = budget.allocate(
            system_and_rules_tokens=3_000,
            tool_tokens=1_500,
            task_state_tokens=1_000,
        )

        self.assertEqual(allocation.repo_map_tokens, 0)
        self.assertEqual(allocation.message_tokens, 4_000)
        self.assertLessEqual(allocation.total_tokens, budget.max_prompt_tokens)

    def test_allocation_rejects_invalid_fixed_content_and_unsatisfiable_minimum(self) -> None:
        budget = PromptBudget(max_prompt_tokens=7_000)
        for field, value in (
            ("system_and_rules_tokens", -1),
            ("tool_tokens", -1),
            ("task_state_tokens", -1),
            ("system_and_rules_tokens", 3_001),
            ("tool_tokens", 2_001),
            ("task_state_tokens", 1_001),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(PromptBudgetError):
                budget.allocate(**{field: value})  # type: ignore[arg-type]
        with self.assertRaises(PromptBudgetError):
            budget.allocate(
                system_and_rules_tokens=3_000,
                tool_tokens=1_500,
                task_state_tokens=1_000,
            )

    def test_budget_models_are_frozen_and_validate_configuration(self) -> None:
        allocation = PromptAllocation(1, 2, 3, 4, 5, 6)
        with self.assertRaises(FrozenInstanceError):
            allocation.message_tokens = 7  # type: ignore[misc]
        for kwargs in (
            {"max_prompt_tokens": 0},
            {"max_message_tokens": 1_999},
            {"min_message_tokens": 12_001},
            {"safety_tokens": -1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(PromptBudgetError):
                PromptBudget(**kwargs)
        self.assertTrue(issubclass(PromptBudgetError, ContextError))

    def test_budget_requires_capacity_for_safety_and_minimum_messages(self) -> None:
        for max_prompt_tokens in (1, 2_499):
            with self.subTest(max_prompt_tokens=max_prompt_tokens), self.assertRaises(
                PromptBudgetError
            ):
                PromptBudget(max_prompt_tokens=max_prompt_tokens)

        boundary = PromptBudget(max_prompt_tokens=2_500)
        self.assertEqual(boundary.allocate().message_tokens, 2_000)


class ContextConfigBudgetCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_config_without_legacy_aliases_keeps_prompt_budget_defaults(self) -> None:
        config = ContextConfig(self.root, self.root, "System")

        self.assertEqual(config.prompt_budget, PromptBudget())
        self.assertEqual(config.repo_map_tokens, 2_000)
        self.assertEqual(config.message_tokens, 12_000)

    def test_legacy_aliases_normalize_to_budget_and_remain_available(self) -> None:
        config = ContextConfig(
            self.root,
            self.root,
            "System",
            repo_map_tokens=120,
            message_tokens=30,
        )

        self.assertEqual(config.repo_map_tokens, 120)
        self.assertEqual(config.message_tokens, 30)
        self.assertEqual(config.prompt_budget.max_repo_map_tokens, 120)
        self.assertEqual(config.prompt_budget.max_message_tokens, 30)
        self.assertEqual(config.prompt_budget.min_message_tokens, 30)

    def test_aliases_conflicting_with_custom_budget_are_rejected(self) -> None:
        budget = PromptBudget(max_repo_map_tokens=100, max_message_tokens=3_000)

        with self.assertRaises(PromptBudgetError):
            ContextConfig(self.root, self.root, "System", prompt_budget=budget, repo_map_tokens=101)
        with self.assertRaises(PromptBudgetError):
            ContextConfig(self.root, self.root, "System", prompt_budget=budget, message_tokens=3_001)


if __name__ == "__main__":
    unittest.main()
