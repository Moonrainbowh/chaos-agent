from __future__ import annotations

import dataclasses
import inspect
import unittest
from datetime import datetime, timedelta, timezone

from code_agent.interfaces.rewind_models import (
    RewindAsOf,
    RewindDisabledReason,
    RewindFacts,
    RewindKind,
    RewindPath,
    RewindPreviewSource,
)


UTC = timezone.utc
DIGEST = "a" * 64


def make_as_of(**changes: object) -> RewindAsOf:
    values = {
        "message_sequence": 0,
        "event_sequence": 1,
        "mutation_sequence": None,
        "coverage_generation": 1,
        "relevant_path_digest": DIGEST,
        "captured_at": datetime(2025, 1, 2, 3, 4, tzinfo=UTC),
    }
    values.update(changes)
    return RewindAsOf(**values)


def make_path(**changes: object) -> RewindPath:
    values = {
        "path": "src/code_agent/main.py",
        "baseline_provenance": "pre-agent",
        "preserves_pre_agent_baseline": True,
    }
    values.update(changes)
    return RewindPath(**values)


def make_facts(**changes: object) -> RewindFacts:
    values = {
        "checkpoint_id": "cp-1",
        "checkpoint_label": "Before refactor",
        "checkpoint_created_at": datetime(2025, 1, 1, tzinfo=UTC),
        "as_of": make_as_of(),
        "conversation_messages": 3,
        "code_paths": (make_path(),),
        "conversation_disabled_reason": None,
        "code_disabled_reason": None,
    }
    values.update(changes)
    return RewindFacts(**values)


class RewindEnumTests(unittest.TestCase):
    def test_kind_wire_values_are_exact(self) -> None:
        self.assertEqual(
            [(item.name, item.value) for item in RewindKind],
            [("CONVERSATION", "conversation"), ("CODE", "code"), ("BOTH", "both")],
        )

    def test_disabled_reason_wire_values_and_priority_are_exact(self) -> None:
        self.assertEqual(
            [item.value for item in RewindDisabledReason],
            [
                "checkpoint-not-found",
                "message-bound-missing",
                "message-bound-invalid",
                "code-coverage-unavailable",
                "code-journal-incomplete",
                "pending-workspace-mutation",
                "snapshot-missing",
                "snapshot-invalid",
                "workspace-conflict",
                "preview-limit-exceeded",
                "source-changed-during-preview",
            ],
        )


class RewindAsOfTests(unittest.TestCase):
    def test_datetimes_are_normalized_to_utc(self) -> None:
        value = make_as_of(
            captured_at=datetime(
                2025, 1, 2, 11, 4, tzinfo=timezone(timedelta(hours=8))
            )
        )
        self.assertEqual(value.captured_at, datetime(2025, 1, 2, 3, 4, tzinfo=UTC))
        self.assertIs(value.captured_at.tzinfo, UTC)

    def test_naive_datetime_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            make_as_of(captured_at=datetime(2025, 1, 2))

    def test_sequences_reject_negative_bool_and_non_int(self) -> None:
        for field in ("message_sequence", "event_sequence", "mutation_sequence"):
            for invalid in (-1, True, 1.0):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises((TypeError, ValueError)):
                        make_as_of(**{field: invalid})

    def test_coverage_generation_is_none_or_positive_true_int(self) -> None:
        for invalid in (0, -1, False, 1.0):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    make_as_of(coverage_generation=invalid)
        self.assertIsNone(make_as_of(coverage_generation=None).coverage_generation)

    def test_digest_is_none_or_lowercase_sha256_hex(self) -> None:
        for invalid in ("a" * 63, "A" * 64, "g" * 64, 7):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    make_as_of(relevant_path_digest=invalid)
        self.assertIsNone(make_as_of(relevant_path_digest=None).relevant_path_digest)


class RewindPathTests(unittest.TestCase):
    def test_accepts_canonical_relative_posix_path(self) -> None:
        value = make_path(path="src/a b/file.py")
        self.assertEqual(value.path, "src/a b/file.py")
        colon_name = make_path(path="namespace:/file.py")
        self.assertEqual(colon_name.path, "namespace:/file.py")

    def test_rejects_noncanonical_or_unsafe_paths(self) -> None:
        invalid_paths = (
            "/root/file",
            "C:/file",
            "C:relative.py",
            "z:folder/file.py",
            "src\\file",
            "src//file",
            "./file",
            "src/./file",
            "src/../file",
            "src/\0file",
        )
        for invalid in invalid_paths:
            with self.subTest(path=invalid):
                with self.assertRaises(ValueError):
                    make_path(path=invalid)

    def test_text_fields_require_bounded_nonblank_strings(self) -> None:
        for field in ("path", "baseline_provenance"):
            for invalid in ("", "   ", "x" * 513, 7):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises((TypeError, ValueError)):
                        make_path(**{field: invalid})

    def test_boolean_field_is_strict(self) -> None:
        for invalid in (0, 1, None, "true"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TypeError):
                    make_path(preserves_pre_agent_baseline=invalid)


class RewindFactsTests(unittest.TestCase):
    def test_normalizes_checkpoint_datetime_and_validates_text(self) -> None:
        value = make_facts(
            checkpoint_created_at=datetime(
                2025, 1, 1, 8, tzinfo=timezone(timedelta(hours=8))
            )
        )
        self.assertEqual(value.checkpoint_created_at, datetime(2025, 1, 1, tzinfo=UTC))
        for field in ("checkpoint_id", "checkpoint_label"):
            for invalid in ("", " ", "x" * 513, 9):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises((TypeError, ValueError)):
                        make_facts(**{field: invalid})

    def test_counts_require_nonnegative_true_ints(self) -> None:
        for invalid in (-1, True, 1.5):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    make_facts(conversation_messages=invalid)

    def test_code_paths_require_exact_tuple_and_exact_unique_paths(self) -> None:
        for invalid in ([make_path()], (object(),), (make_path(), make_path())):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    make_facts(code_paths=invalid)

        class PathSubclass(RewindPath):
            pass

        subclass = PathSubclass("other.py", "baseline", True)
        with self.assertRaises(TypeError):
            make_facts(code_paths=(subclass,))

    def test_facet_reasons_reject_wrong_category(self) -> None:
        with self.assertRaises(ValueError):
            make_facts(
                conversation_disabled_reason=RewindDisabledReason.SNAPSHOT_MISSING,
                conversation_messages=0,
            )
        with self.assertRaises(ValueError):
            make_facts(
                code_disabled_reason=RewindDisabledReason.MESSAGE_BOUND_MISSING,
                code_paths=(),
            )

    def test_disabled_facets_cannot_contain_partial_results(self) -> None:
        with self.assertRaises(ValueError):
            make_facts(
                conversation_disabled_reason=RewindDisabledReason.MESSAGE_BOUND_MISSING
            )
        with self.assertRaises(ValueError):
            make_facts(code_disabled_reason=RewindDisabledReason.SNAPSHOT_MISSING)

    def test_conversation_source_change_also_clears_code_facts(self) -> None:
        with self.assertRaises(ValueError):
            make_facts(
                conversation_disabled_reason=(
                    RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW
                ),
                conversation_messages=0,
            )

    def test_code_source_change_also_clears_conversation_facts(self) -> None:
        with self.assertRaises(ValueError):
            make_facts(
                code_disabled_reason=(
                    RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW
                ),
                code_paths=(),
            )

    def test_as_of_and_reasons_require_exact_public_types(self) -> None:
        for changes in (
            {"as_of": object()},
            {"conversation_disabled_reason": "message-bound-missing"},
            {"code_disabled_reason": "snapshot-missing"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(TypeError):
                    make_facts(**changes)


class RewindProtocolTests(unittest.TestCase):
    def test_public_protocol_surface_is_read_only_and_exact(self) -> None:
        public_methods = {
            name
            for name, value in RewindPreviewSource.__dict__.items()
            if not name.startswith("_") and inspect.isfunction(value)
        }
        self.assertEqual(public_methods, {"list_candidates", "preview"})
        self.assertTrue(inspect.iscoroutinefunction(RewindPreviewSource.list_candidates))
        self.assertTrue(inspect.iscoroutinefunction(RewindPreviewSource.preview))
        for forbidden in ("apply", "restore", "confirm", "mutate"):
            self.assertFalse(hasattr(RewindPreviewSource, forbidden))


if __name__ == "__main__":
    unittest.main()
