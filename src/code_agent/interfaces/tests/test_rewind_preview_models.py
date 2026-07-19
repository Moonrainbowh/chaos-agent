from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timezone

from code_agent.interfaces.rewind_models import (
    RewindAsOf,
    RewindCheckpointCandidate,
    RewindCheckpointPage,
    RewindDisabledReason,
    RewindFacts,
    RewindKind,
    RewindPath,
    RewindPreview,
)


UTC = timezone.utc


def make_as_of() -> RewindAsOf:
    return RewindAsOf(1, 2, 3, 1, "a" * 64, datetime(2025, 1, 2, tzinfo=UTC))


def make_path() -> RewindPath:
    return RewindPath("src/main.py", "pre-agent", True)


def make_preview(**changes: object) -> RewindPreview:
    values = {
        "kind": RewindKind.BOTH,
        "checkpoint_id": "cp-1",
        "checkpoint_label": "Checkpoint",
        "as_of": make_as_of(),
        "conversation_messages": 2,
        "code_paths": (make_path(),),
        "disabled_reasons": (),
        "enabled": True,
        "requires_confirmation": True,
        "apply_available": False,
        "requires_git_reset": False,
    }
    values.update(changes)
    return RewindPreview(**values)


class RewindPreviewReasonTests(unittest.TestCase):
    def test_disabled_reasons_require_exact_ordered_unique_enum_tuple(self) -> None:
        source_changed = RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW
        missing = RewindDisabledReason.CHECKPOINT_NOT_FOUND
        invalid_values = (
            [missing],
            ("checkpoint-not-found",),
            (source_changed, missing),
            (missing, missing),
        )
        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    make_preview(
                        disabled_reasons=invalid,
                        conversation_messages=0,
                        code_paths=(),
                        enabled=False,
                        requires_confirmation=False,
                    )

    def test_reasons_must_apply_to_requested_kind(self) -> None:
        with self.assertRaises(ValueError):
            make_preview(
                kind=RewindKind.CONVERSATION,
                disabled_reasons=(RewindDisabledReason.SNAPSHOT_MISSING,),
                enabled=False,
                requires_confirmation=False,
            )
        with self.assertRaises(ValueError):
            make_preview(
                kind=RewindKind.CODE,
                disabled_reasons=(RewindDisabledReason.MESSAGE_BOUND_MISSING,),
                enabled=False,
                requires_confirmation=False,
            )

    def test_both_preview_rejects_partial_results_for_facet_reason(self) -> None:
        with self.assertRaises(ValueError):
            make_preview(
                disabled_reasons=(RewindDisabledReason.MESSAGE_BOUND_INVALID,),
                enabled=False,
                requires_confirmation=False,
            )
        with self.assertRaises(ValueError):
            make_preview(
                disabled_reasons=(RewindDisabledReason.WORKSPACE_CONFLICT,),
                enabled=False,
                requires_confirmation=False,
            )

    def test_global_reasons_clear_both_facets(self) -> None:
        for reason in (
            RewindDisabledReason.CHECKPOINT_NOT_FOUND,
            RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW,
        ):
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError):
                    make_preview(
                        disabled_reasons=(reason,),
                        conversation_messages=1,
                        code_paths=(),
                        enabled=False,
                        requires_confirmation=False,
                    )
                with self.assertRaises(ValueError):
                    make_preview(
                        disabled_reasons=(reason,),
                        conversation_messages=0,
                        code_paths=(make_path(),),
                        enabled=False,
                        requires_confirmation=False,
                    )

    def test_code_only_preview_limit_preserves_conversation_count(self) -> None:
        preview = make_preview(
            disabled_reasons=(RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED,),
            code_paths=(),
            enabled=False,
            requires_confirmation=False,
        )
        self.assertEqual(preview.conversation_messages, 2)
        self.assertEqual(preview.code_paths, ())


class RewindPreviewFieldTests(unittest.TestCase):
    def test_enabled_and_confirmation_are_derived_by_invariant(self) -> None:
        for changes in (
            {"disabled_reasons": (), "enabled": False},
            {
                "disabled_reasons": (RewindDisabledReason.MESSAGE_BOUND_MISSING,),
                "conversation_messages": 0,
                "enabled": True,
            },
            {"disabled_reasons": (), "requires_confirmation": False},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    make_preview(**changes)

    def test_flags_are_strict_booleans_and_apply_flags_are_always_false(self) -> None:
        for field in (
            "enabled",
            "requires_confirmation",
            "apply_available",
            "requires_git_reset",
        ):
            with self.subTest(field=field):
                with self.assertRaises(TypeError):
                    make_preview(**{field: 0})
        for field in ("apply_available", "requires_git_reset"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    make_preview(**{field: True})

    def test_kind_as_of_and_tuple_members_require_exact_types(self) -> None:
        for changes in (
            {"kind": "both"},
            {"as_of": object()},
            {"code_paths": [make_path()]},
            {"code_paths": (object(),)},
            {"code_paths": (make_path(), make_path())},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises((TypeError, ValueError)):
                    make_preview(**changes)

    def test_text_and_count_fields_are_validated(self) -> None:
        for field in ("checkpoint_id", "checkpoint_label"):
            for invalid in ("", " ", "x" * 513, 4):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises((TypeError, ValueError)):
                        make_preview(**{field: invalid})
        for invalid in (-1, True, 1.5):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    make_preview(conversation_messages=invalid)


class AllRewindRecordsFrozenTests(unittest.TestCase):
    def test_all_six_records_reject_assignment(self) -> None:
        records_and_fields = (
            (make_as_of(), "message_sequence"),
            (make_path(), "path"),
            (
                RewindFacts(
                    "cp", "label", datetime.now(UTC), make_as_of(), 0, (), None, None
                ),
                "checkpoint_id",
            ),
            (make_preview(), "enabled"),
            (
                RewindCheckpointCandidate(
                    "cp", "label", datetime.now(UTC), True, False
                ),
                "label",
            ),
            (RewindCheckpointPage((), None), "next_cursor"),
        )
        for record, field in records_and_fields:
            with self.subTest(record=type(record).__name__, field=field):
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(record, field, None)


if __name__ == "__main__":
    unittest.main()
