from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timedelta, timezone

from code_agent.interfaces.rewind_models import (
    RewindCheckpointCandidate,
    RewindCheckpointPage,
)


UTC = timezone.utc


def make_candidate(number: int = 1, **changes: object) -> RewindCheckpointCandidate:
    values = {
        "checkpoint_id": f"cp-{number}",
        "label": f"Checkpoint {number}",
        "created_at": datetime(2025, 1, number, tzinfo=UTC),
        "has_message_bound": True,
        "has_code_anchor": False,
    }
    values.update(changes)
    return RewindCheckpointCandidate(**values)


class RewindCheckpointCandidateTests(unittest.TestCase):
    def test_metadata_is_frozen_and_datetime_is_normalized(self) -> None:
        candidate = make_candidate(
            created_at=datetime(
                2025, 1, 1, 8, tzinfo=timezone(timedelta(hours=8))
            )
        )
        self.assertEqual(candidate.created_at, datetime(2025, 1, 1, tzinfo=UTC))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            candidate.label = "changed"

    def test_created_at_requires_aware_datetime(self) -> None:
        for invalid in (datetime(2025, 1, 1), "2025-01-01"):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    make_candidate(created_at=invalid)

    def test_text_fields_are_bounded_nonblank_strings(self) -> None:
        for field in ("checkpoint_id", "label"):
            for invalid in ("", " ", "x" * 513, 3):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises((TypeError, ValueError)):
                        make_candidate(**{field: invalid})

    def test_anchor_flags_are_strict_booleans(self) -> None:
        for field in ("has_message_bound", "has_code_anchor"):
            for invalid in (0, 1, None, "false"):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(TypeError):
                        make_candidate(**{field: invalid})


class RewindCheckpointPageTests(unittest.TestCase):
    def test_accepts_empty_blank_and_opaque_cursors_unchanged(self) -> None:
        for cursor in ("", "   ", "opaque+/= token"):
            with self.subTest(cursor=cursor):
                page = RewindCheckpointPage(items=(), next_cursor=cursor)
                self.assertEqual(page.next_cursor, cursor)

    def test_items_require_exact_tuple_and_exact_candidate_type(self) -> None:
        for invalid in ([make_candidate()], (object(),)):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TypeError):
                    RewindCheckpointPage(items=invalid, next_cursor=None)

        class CandidateSubclass(RewindCheckpointCandidate):
            pass

        subclass = CandidateSubclass("cp-x", "label", datetime.now(UTC), True, True)
        with self.assertRaises(TypeError):
            RewindCheckpointPage(items=(subclass,), next_cursor=None)

    def test_rejects_duplicate_checkpoint_ids_and_more_than_100_items(self) -> None:
        with self.assertRaises(ValueError):
            RewindCheckpointPage(
                items=(make_candidate(1), make_candidate(1)), next_cursor=None
            )
        with self.assertRaises(ValueError):
            RewindCheckpointPage(
                items=tuple(make_candidate(index) for index in range(1, 102)),
                next_cursor=None,
            )

    def test_cursor_is_none_or_arbitrary_bounded_string(self) -> None:
        for invalid in ("x" * 1025, 1, b"cursor"):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    RewindCheckpointPage(items=(), next_cursor=invalid)


if __name__ == "__main__":
    unittest.main()
