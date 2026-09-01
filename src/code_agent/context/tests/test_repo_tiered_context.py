from __future__ import annotations

import unittest

from code_agent.context.models import FileSignature, RepoEntry, RepoRelation, Symbol
from code_agent.context.repo_index import RepoIndexSnapshot
from code_agent.context.repo_tiered_context import (
    render_tier_selection,
    select_tiered_context,
)
from code_agent.context.tokens import estimate_tokens


class TieredRepoContextTests(unittest.TestCase):
    def snapshot(self) -> RepoIndexSnapshot:
        target = RepoEntry(
            "src/pkg/service.py",
            (
                Symbol(
                    "src/pkg/service.py",
                    "serve",
                    "function",
                    10,
                    20,
                    "def serve(request):",
                    "Serve one request.",
                ),
            ),
            (),
            100,
            FileSignature(100, 1),
        )
        caller = RepoEntry(
            "src/pkg/api.py",
            (Symbol("src/pkg/api.py", "handle", "function", 5, 8, "def handle():"),),
            ("src/pkg/service.py",),
            80,
            FileSignature(80, 1),
            (
                RepoRelation(
                    "call",
                    "handle",
                    7,
                    "src/pkg/service.py",
                    "serve",
                    10,
                    20,
                    "exact",
                    "direct call",
                ),
            ),
        )
        test = RepoEntry(
            "tests/test_service.py",
            (Symbol("tests/test_service.py", "test_serve", "function", 4, 6, "def test_serve():"),),
            ("src/pkg/service.py",),
            60,
            FileSignature(60, 1),
            (
                RepoRelation(
                    "call",
                    "test_serve",
                    5,
                    "src/pkg/service.py",
                    "serve",
                    10,
                    20,
                    "exact",
                    "test call",
                ),
            ),
        )
        return RepoIndexSnapshot(7, (target, caller, test))

    def test_test_impact_is_derived_for_the_request_without_persisted_edge(self) -> None:
        snapshot = self.snapshot()

        selection = select_tiered_context(
            snapshot,
            snapshot.entries,
            "repair serve in src/pkg/service.py",
            (),
            300,
        )

        self.assertEqual(selection.l0[0].path, "src/pkg/service.py")
        self.assertIn("tests/test_service.py", {item.path for item in selection.l1})
        self.assertTrue(
            any("test impact candidate" in reason for item in selection.l1 for reason in item.reasons)
        )
        self.assertTrue(
            all(edge.kind != "test_impact" for entry in snapshot.entries for edge in entry.relations)
        )

    def test_module_level_traceback_becomes_bounded_line_anchor(self) -> None:
        entry = RepoEntry(
            "module.py",
            (Symbol("module.py", "later", "function", 50, 60, "def later():"),),
            (),
            1_000,
            FileSignature(1_000, 1),
        )
        snapshot = RepoIndexSnapshot(1, (entry,))

        selection = select_tiered_context(snapshot, (entry,), "module.py:5", (), 200)

        anchor = selection.l0[0]
        self.assertEqual(anchor.kind, "line")
        self.assertEqual((anchor.start_line, anchor.end_line), (1, 25))
        self.assertLessEqual(anchor.end_line - anchor.start_line + 1, 80)

    def test_same_inputs_pack_and_render_byte_identically_within_budget(self) -> None:
        snapshot = self.snapshot()

        first = select_tiered_context(snapshot, snapshot.entries, "serve", (), 120)
        second = select_tiered_context(snapshot, snapshot.entries, "serve", (), 120)
        first_text = render_tier_selection(first, 120)
        second_text = render_tier_selection(second, 120)

        self.assertEqual(first, second)
        self.assertEqual(first_text.encode("utf-8"), second_text.encode("utf-8"))
        self.assertLessEqual(estimate_tokens(first_text), 120)
        self.assertLessEqual(len(first.l0), 2)
        self.assertLessEqual(len(first.l1), 8)
        self.assertLessEqual(len(first.l2), 16)

    def test_path_anchor_accepts_spaces_and_uses_relation_source_symbol(self) -> None:
        target = RepoEntry(
            "src/含 空格.py",
            (Symbol("src/含 空格.py", "first", "function", 1, 2),
             Symbol("src/含 空格.py", "wanted", "function", 10, 12)),
            (), 100, FileSignature(100, 1),
        )
        caller = RepoEntry(
            "caller.py",
            (Symbol("caller.py", "call_wanted", "function", 5, 7),
             Symbol("caller.py", "unrelated", "function", 20, 22)),
            ("src/含 空格.py",), 80, FileSignature(80, 1),
            (RepoRelation("call", "call_wanted", 6, "src/含 空格.py", "wanted", 10, 12),),
        )
        snapshot = RepoIndexSnapshot(3, (target, caller))
        selection = select_tiered_context(
            snapshot, (target, caller), "src/含 空格.py:11", (), 2_000
        )
        self.assertEqual(selection.l0[0].symbol, "wanted")
        self.assertEqual(selection.l1[0].symbol, "call_wanted")

    def test_large_symbol_renders_generation_bound_split_targets(self) -> None:
        entry = RepoEntry(
            "huge.py", (Symbol("huge.py", "huge", "function", 1, 950),),
            (), 10_000, FileSignature(10_000, 55),
        )
        selection = select_tiered_context(
            RepoIndexSnapshot(9, (entry,)), (entry,), "huge", (), 2_000
        )
        rendered = render_tier_selection(selection, 2_000)
        self.assertIn('\"generation\":9', rendered)
        self.assertIn('\"expected_modified_ns\":55', rendered)
        self.assertEqual(
            [(item.start_line, item.end_line) for item in selection.overflow_targets],
            [(1, 400), (401, 800), (801, 950)],
        )


if __name__ == "__main__":
    unittest.main()
