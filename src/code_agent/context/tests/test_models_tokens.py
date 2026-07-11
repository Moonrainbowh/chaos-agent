from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.errors import (  # noqa: E402
    ContextBudgetError,
    ContextError,
    RepoMapError,
    RuleLimitError,
)
from code_agent.context.models import (  # noqa: E402
    CompactionResult,
    ContextConfig,
    ProjectRule,
    RepoEntry,
    Symbol,
)
from code_agent.context.tokens import estimate_tokens, truncate_to_tokens  # noqa: E402
from code_agent.core.models import Message  # noqa: E402


class ContextModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_context_errors_share_a_feature_base(self) -> None:
        for error_type in (RuleLimitError, ContextBudgetError, RepoMapError):
            self.assertTrue(issubclass(error_type, ContextError))

    def test_config_is_frozen_and_validates_stable_inputs_and_positive_limits(
        self,
    ) -> None:
        config = ContextConfig(
            workspace_root=self.root,
            cwd=self.root,
            system_prompt="Work carefully.",
            max_rule_bytes=10,
            max_rules_total=20,
            repo_scan=30,
            repo_map_tokens=40,
            message_tokens=50,
            recent_messages=2,
        )

        self.assertEqual(config.workspace_root, self.root)
        with self.assertRaises(FrozenInstanceError):
            config.cwd = self.root / "other"  # type: ignore[misc]
        for field in (
            "max_rule_bytes",
            "max_rules_total",
            "repo_scan",
            "repo_map_tokens",
            "message_tokens",
            "recent_messages",
        ):
            values = config.__dict__ | {field: 0}
            with self.subTest(field=field), self.assertRaises(ValueError):
                ContextConfig(**values)
        with self.assertRaises(ValueError):
            ContextConfig(self.root, self.root, "  ")

    def test_context_value_models_are_immutable_and_tuple_backed(self) -> None:
        symbol = Symbol(path="pkg/mod.py", name="run", kind="function", line=3)
        entry = RepoEntry(
            path="pkg/mod.py",
            symbols=[symbol],
            dependencies=["pkg/base.py"],
            size_bytes=12,
        )
        result = CompactionResult(
            messages=[Message(role="user", content="hello")],
            removed_count=1,
            estimated_tokens=2,
            summary="checkpoint",
        )

        self.assertEqual(entry.symbols, (symbol,))
        self.assertEqual(entry.dependencies, ("pkg/base.py",))
        self.assertIsInstance(result.messages, tuple)
        self.assertEqual(ProjectRule("AGENTS.md", "rule", 0).scope_depth, 0)
        with self.assertRaises(FrozenInstanceError):
            symbol.line = 9  # type: ignore[misc]


class TokenTests(unittest.TestCase):
    def test_estimator_is_deterministic_for_empty_ascii_and_multibyte_text(self) -> None:
        samples = {
            "": 0,
            "abcd": 1,
            "hello world": 3,
            "中文测试": 4,
            "😀": 2,
        }
        for value, expected in samples.items():
            with self.subTest(value=value):
                self.assertEqual(estimate_tokens(value), expected)
                self.assertEqual(estimate_tokens(value), estimate_tokens(value))

    def test_truncation_never_exceeds_budget_and_respects_boundaries(self) -> None:
        cases = (
            ("abcdef", 1, "abcd"),
            ("中文测试", 2, "中文"),
            ("abc", 0, ""),
            ("abc", 1, "abc"),
        )
        for value, budget, expected in cases:
            with self.subTest(value=value, budget=budget):
                truncated = truncate_to_tokens(value, budget)
                self.assertEqual(truncated, expected)
                self.assertLessEqual(estimate_tokens(truncated), budget)

    def test_surrogate_pair_is_kept_or_removed_as_one_character(self) -> None:
        pair = "\ud83d\ude00"

        self.assertEqual(truncate_to_tokens(pair + "x", 1), "")
        self.assertEqual(truncate_to_tokens(pair + "x", 2), pair)

    def test_token_api_rejects_non_text_and_invalid_budgets(self) -> None:
        with self.assertRaises(TypeError):
            estimate_tokens(3)  # type: ignore[arg-type]
        for value in (-1, True, 1.5):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                truncate_to_tokens("text", value)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
