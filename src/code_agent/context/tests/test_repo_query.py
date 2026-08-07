from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.repo_query import (  # noqa: E402
    bound_repo_query,
    plan_repo_query,
)


class RepoQueryPlanTests(unittest.TestCase):
    def test_channels_are_bounded_and_keep_tail_intent(self) -> None:
        plan = plan_repo_query(
            "你好请帮我仔细分析这个项目中究竟在哪里负责"
            "危险命令执行前权限校验的代码"
        )

        self.assertLessEqual(len(plan.terms), 16)
        self.assertLessEqual(len(plan.trigrams), 16)
        self.assertLessEqual(len(plan.shorts), 8)
        self.assertIn("权限校", plan.trigrams)
        self.assertIn("权限", plan.shorts)

    def test_short_ascii_is_an_auxiliary_literal(self) -> None:
        plan = plan_repo_query('db OR "权限" *')

        self.assertIn("db", plan.shorts)
        self.assertIn("权限", plan.shorts)

    def test_chinese_intent_adds_bounded_english_code_aliases(self) -> None:
        plan = plan_repo_query("权限校验在哪里实现")

        self.assertIn("policy", plan.terms)
        self.assertIn("approval", plan.terms)
        self.assertIn("validation", plan.terms)
        self.assertLessEqual(len(plan.terms), 16)

    def test_raw_query_prefix_is_bounded_for_all_channels(self) -> None:
        query = "x" * 600

        self.assertEqual(bound_repo_query(query), query[:512])

    def test_many_distinct_cjk_characters_still_keep_tail_intent(self) -> None:
        query = "".join(chr(0x4E00 + offset) for offset in range(220))

        plan = plan_repo_query(query)

        self.assertIn(query[-3:], plan.trigrams)


if __name__ == "__main__":
    unittest.main()
