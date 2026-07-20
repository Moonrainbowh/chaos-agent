from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from code_agent.interfaces.rewind_models import (
    RewindCheckpointCandidate,
    RewindCheckpointPage,
)
from code_agent.interfaces.rewind_view import render_rewind_candidates


UTC = timezone.utc


def make_candidate(
    checkpoint_id: str = "cp-1",
    label: str = "Before edit",
    *,
    offset_hours: int = 0,
    message_bound: bool = True,
    code_anchor: bool = False,
) -> RewindCheckpointCandidate:
    zone = timezone(timedelta(hours=offset_hours))
    return RewindCheckpointCandidate(
        checkpoint_id,
        label,
        datetime(2025, 1, 2, 11, 4, 5, tzinfo=zone),
        message_bound,
        code_anchor,
    )


class RewindCandidateRenderTests(unittest.TestCase):
    def test_candidates_render_all_facets_in_stable_order(self) -> None:
        page = RewindCheckpointPage(
            (
                make_candidate(offset_hours=8),
                make_candidate(
                    "cp-2",
                    "After tool",
                    message_bound=False,
                    code_anchor=True,
                ),
            ),
            "cursor-2",
        )
        self.assertEqual(
            render_rewind_candidates(page).splitlines(),
            [
                "rewind · candidate only",
                "checkpoint candidates: 2",
                (
                    "- cp-1 · 2025-01-02T03:04:05Z · Before edit"
                    " · message-bound yes · code anchor no"
                ),
                (
                    "- cp-2 · 2025-01-02T11:04:05Z · After tool"
                    " · message-bound no · code anchor yes"
                ),
                "opaque next cursor: cursor-2",
            ],
        )

    def test_empty_page_and_none_cursor_use_dash(self) -> None:
        rendered = render_rewind_candidates(RewindCheckpointPage((), None))
        self.assertEqual(
            rendered.splitlines(),
            [
                "rewind · candidate only",
                "checkpoint candidates: 0",
                "opaque next cursor: -",
            ],
        )

    def test_item_limit_reports_hidden_count_from_full_page(self) -> None:
        page = RewindCheckpointPage(
            tuple(make_candidate(f"cp-{index}") for index in range(3)),
            None,
        )
        rendered = render_rewind_candidates(page, max_items=1)
        self.assertIn("- cp-0", rendered)
        self.assertNotIn("- cp-1", rendered)
        self.assertIn("... 2 candidates hidden", rendered)

    def test_untrusted_candidate_and_cursor_text_cannot_inject_lines(self) -> None:
        page = RewindCheckpointPage(
            (
                make_candidate(
                    "cp\t\x1b[2J\u2028fake",
                    "label\t\r\nopaque next cursor: forged\u2029tail",
                ),
            ),
            "next\t\r\ncheckpoint candidates: 99\u2028end",
        )
        rendered = render_rewind_candidates(page)
        for forbidden in ("\x1b", "\t", "\r", "\u2028", "\u2029"):
            with self.subTest(forbidden=repr(forbidden)):
                self.assertNotIn(forbidden, rendered)
        self.assertEqual(len(rendered.splitlines()), 4)
        self.assertEqual(rendered.count("checkpoint candidates:"), 2)
        self.assertEqual(rendered.count("opaque next cursor:"), 2)

    def test_empty_and_blank_opaque_cursors_are_preserved_safely(self) -> None:
        empty = render_rewind_candidates(RewindCheckpointPage((), ""))
        blank = render_rewind_candidates(RewindCheckpointPage((), "  "))
        self.assertTrue(empty.endswith("opaque next cursor: "))
        self.assertTrue(blank.endswith("opaque next cursor:   "))

    def test_candidate_limits_fail_closed_and_accept_boundaries(self) -> None:
        page = RewindCheckpointPage((make_candidate(),), None)
        for invalid in (False, 1.0, None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TypeError):
                    render_rewind_candidates(page, max_items=invalid)
        for invalid in (-1, 21):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    render_rewind_candidates(page, max_items=invalid)
        self.assertIn("... 1 candidates hidden", render_rewind_candidates(page, max_items=0))
        self.assertNotIn("hidden", render_rewind_candidates(page, max_items=20))


if __name__ == "__main__":
    unittest.main()
