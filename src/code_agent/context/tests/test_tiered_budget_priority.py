from __future__ import annotations

import json
import unittest
from dataclasses import replace

from code_agent.context.models import FileSignature
from code_agent.context.repo_tiered_context import (
    TOKENIZER_VERSION, TierNode, TierSelection, _pack, _render,
    render_tier_selection,
)
from code_agent.context.tokens import estimate_tokens


class TieredBudgetPriorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.anchor = TierNode(
            "target.py", "symbol", 1, 20, "target", FileSignature(1200, 55, 7, 8),
            "def target():", reasons=("task seed",),
        )
        self.source = "def target():\n" + "    value = '关键源码\\n'\n" * 18 + "    return value\n"
        self.sources = {("target.py", 1, 20): self.source}
        self.base = TierSelection(7, TOKENIZER_VERSION, (self.anchor,))
        self.budget = estimate_tokens(_render(self.base, self.sources))
        self.neighbors = tuple(
            replace(self.anchor, path=f"neighbor_{i:02}.py", distance=1)
            for i in range(32)
        )

    def records(self, text):
        return [json.loads(line) for line in text.splitlines()[1:]]

    def test_real_l0_survives_arbitrary_lower_tier_pressure(self) -> None:
        for count in (1, 8, 32):
            with self.subTest(count=count):
                selection = replace(
                    self.base, l1=self.neighbors[:count], l2=self.neighbors[:count],
                )
                text = render_tier_selection(selection, self.budget, self.sources)
                self.assertEqual(text, _render(self.base, self.sources))
                self.assertEqual(self.records(text)[1]["source"], self.source)
                self.assertLessEqual(estimate_tokens(text), self.budget)
                self.assertEqual(text, render_tier_selection(selection, self.budget, self.sources))

    def test_pack_does_not_reserve_lower_tier_quotas_at_l0_expense(self) -> None:
        budget = estimate_tokens(_render(self.base))
        selection = _pack(7, (self.anchor,), self.neighbors, self.neighbors, budget)
        self.assertEqual(selection.l0, (self.anchor,))
        text = render_tier_selection(selection, self.budget, self.sources)
        self.assertEqual(self.records(text)[1]["source"], self.source)

    def test_l2_is_removed_before_l1_and_l1_keeps_input_priority(self) -> None:
        expected = replace(self.base, l1=self.neighbors[:1])
        budget = estimate_tokens(_render(expected, self.sources))
        selection = replace(self.base, l1=self.neighbors[:8], l2=self.neighbors[8:])
        self.assertEqual(
            render_tier_selection(selection, budget, self.sources),
            _render(expected, self.sources),
        )

    def test_deferred_targets_cannot_displace_fitting_l0(self) -> None:
        selection = replace(self.base, overflow_targets=self.neighbors)
        self.assertEqual(
            render_tier_selection(selection, self.budget, self.sources),
            _render(self.base, self.sources),
        )

    def test_only_unfittable_l0_is_downgraded_with_signature_bound_target(self) -> None:
        sources = {("target.py", 1, 20): "long source " * 2000}
        text = render_tier_selection(self.base, 500, sources)
        records = self.records(text)
        self.assertEqual(records[0], {"generation": 7, "tokenizer_version": TOKENIZER_VERSION})
        self.assertEqual(records[1]["tier"], "L1")
        target = records[2]
        self.assertEqual(target["kind"], "read_code_slice_target")
        self.assertEqual((target["start_line"], target["end_line"]), (1, 20))
        self.assertEqual(target["expected_modified_ns"], 55)
        self.assertEqual(target["expected_size_bytes"], 1200)
        self.assertEqual(target["expected_device_id"], 7)
        self.assertEqual(target["expected_file_id"], 8)
        self.assertLessEqual(estimate_tokens(text), 500)

    def test_second_l0_cannot_displace_fitting_first_l0(self) -> None:
        second = self.neighbors[0]
        sources = {**self.sources, (second.path, 1, 20): self.source}
        selection = replace(self.base, l0=(self.anchor, second))
        self.assertEqual(
            render_tier_selection(selection, self.budget, sources),
            _render(self.base, sources),
        )

    def test_all_small_budgets_respect_hard_limit(self) -> None:
        selection = replace(self.base, l1=self.neighbors, l2=self.neighbors)
        for budget in (0, 1, 20, 80, 120, 200, self.budget - 1, self.budget):
            with self.subTest(budget=budget):
                text = render_tier_selection(selection, budget, self.sources)
                self.assertLessEqual(estimate_tokens(text), budget)
