from __future__ import annotations

import unittest
from datetime import datetime, timezone

from code_agent.interfaces.rewind_models import (
    RewindAsOf,
    RewindDisabledReason,
    RewindFacts,
    RewindKind,
    RewindPath,
)
from code_agent.interfaces.rewind_view import (
    build_rewind_preview,
    render_rewind_preview,
)


UTC = timezone.utc


def make_as_of(*, observed: bool = True) -> RewindAsOf:
    return RewindAsOf(
        7 if observed else None,
        8 if observed else None,
        9 if observed else None,
        2 if observed else None,
        "a" * 64 if observed else None,
        datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC),
    )


def make_paths() -> tuple[RewindPath, ...]:
    return (
        RewindPath("src/main.py", "pre-agent", True),
        RewindPath("src/new.py", "created-by-agent", False),
    )


def make_facts(**changes: object) -> RewindFacts:
    values = {
        "checkpoint_id": "cp-1",
        "checkpoint_label": "Before edit",
        "checkpoint_created_at": datetime(2025, 1, 2, tzinfo=UTC),
        "as_of": make_as_of(),
        "conversation_messages": 3,
        "code_paths": make_paths(),
        "conversation_disabled_reason": None,
        "code_disabled_reason": None,
    }
    values.update(changes)
    return RewindFacts(**values)


class RewindProjectionTests(unittest.TestCase):
    def test_each_kind_selects_only_its_requested_facets(self) -> None:
        conversation = build_rewind_preview(
            RewindKind.CONVERSATION, make_facts()
        )
        code = build_rewind_preview(RewindKind.CODE, make_facts())
        both = build_rewind_preview(RewindKind.BOTH, make_facts())
        self.assertEqual(conversation.conversation_messages, 3)
        self.assertEqual(conversation.code_paths, ())
        self.assertEqual(code.conversation_messages, 0)
        self.assertEqual(code.code_paths, make_paths())
        self.assertEqual(both.conversation_messages, 3)
        self.assertEqual(both.code_paths, make_paths())

    def test_facet_failures_do_not_disable_unselected_facet(self) -> None:
        message_failed = make_facts(
            conversation_messages=0,
            conversation_disabled_reason=(
                RewindDisabledReason.MESSAGE_BOUND_MISSING
            ),
        )
        code_failed = make_facts(
            code_paths=(),
            code_disabled_reason=RewindDisabledReason.SNAPSHOT_MISSING,
        )
        self.assertTrue(
            build_rewind_preview(RewindKind.CODE, message_failed).enabled
        )
        self.assertTrue(
            build_rewind_preview(
                RewindKind.CONVERSATION, code_failed
            ).enabled
        )

    def test_both_merges_reasons_in_enum_order_without_duplicates(self) -> None:
        facts = make_facts(
            conversation_messages=0,
            code_paths=(),
            conversation_disabled_reason=(
                RewindDisabledReason.MESSAGE_BOUND_INVALID
            ),
            code_disabled_reason=RewindDisabledReason.SNAPSHOT_MISSING,
        )
        preview = build_rewind_preview(RewindKind.BOTH, facts)
        self.assertEqual(
            preview.disabled_reasons,
            (
                RewindDisabledReason.MESSAGE_BOUND_INVALID,
                RewindDisabledReason.SNAPSHOT_MISSING,
            ),
        )
        self.assertFalse(preview.enabled)
        self.assertFalse(preview.requires_confirmation)

    def test_global_reason_is_deduplicated(self) -> None:
        reason = RewindDisabledReason.CHECKPOINT_NOT_FOUND
        facts = make_facts(
            conversation_messages=0,
            code_paths=(),
            conversation_disabled_reason=reason,
            code_disabled_reason=reason,
        )
        preview = build_rewind_preview(RewindKind.BOTH, facts)
        self.assertEqual(preview.disabled_reasons, (reason,))
        self.assertEqual(preview.conversation_messages, 0)
        self.assertEqual(preview.code_paths, ())

    def test_zero_change_preview_remains_enabled_and_read_only(self) -> None:
        preview = build_rewind_preview(
            RewindKind.BOTH,
            make_facts(conversation_messages=0, code_paths=()),
        )
        self.assertTrue(preview.enabled)
        self.assertTrue(preview.requires_confirmation)
        self.assertFalse(preview.apply_available)
        self.assertFalse(preview.requires_git_reset)

    def test_code_limit_preserves_both_conversation_count(self) -> None:
        preview = build_rewind_preview(
            RewindKind.BOTH,
            make_facts(
                code_paths=(),
                code_disabled_reason=(
                    RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED
                ),
            ),
        )
        self.assertEqual(preview.conversation_messages, 3)
        self.assertEqual(preview.code_paths, ())


class RewindPreviewRenderTests(unittest.TestCase):
    def test_enabled_preview_has_stable_complete_output(self) -> None:
        rendered = render_rewind_preview(
            build_rewind_preview(RewindKind.BOTH, make_facts())
        )
        self.assertEqual(
            rendered.splitlines(),
            [
                "rewind · preview only",
                "checkpoint: cp-1 · Before edit",
                "kind: both",
                (
                    "as of: 2025-01-02T03:04:05Z · message=7 · event=8"
                    " · mutation=9 · coverage=2 · paths=" + "a" * 64
                ),
                "conversation messages: 3",
                "code paths: 2",
                "pre-agent baselines preserved: 1",
                (
                    "- src/main.py · baseline pre-agent"
                    " · preserves pre-agent baseline"
                ),
                (
                    "- src/new.py · baseline created-by-agent"
                    " · preserves pre-agent baseline"
                ),
                "state: enabled",
                "confirmation required: yes",
                "apply unavailable",
                "no git reset",
            ],
        )

    def test_none_observations_render_as_dashes(self) -> None:
        preview = build_rewind_preview(
            RewindKind.CONVERSATION,
            make_facts(as_of=make_as_of(observed=False), code_paths=()),
        )
        rendered = render_rewind_preview(preview)
        self.assertIn(
            "message=- · event=- · mutation=- · coverage=- · paths=-",
            rendered,
        )

    def test_unavailable_conversation_depends_on_selected_facet_reason(self) -> None:
        code = build_rewind_preview(RewindKind.CODE, make_facts())
        message_failed = build_rewind_preview(
            RewindKind.BOTH,
            make_facts(
                conversation_messages=0,
                conversation_disabled_reason=(
                    RewindDisabledReason.MESSAGE_BOUND_INVALID
                ),
            ),
        )
        code_failed = build_rewind_preview(
            RewindKind.BOTH,
            make_facts(
                code_paths=(),
                code_disabled_reason=RewindDisabledReason.SNAPSHOT_INVALID,
            ),
        )
        self.assertIn("conversation messages: unavailable", render_rewind_preview(code))
        self.assertIn(
            "conversation messages: unavailable",
            render_rewind_preview(message_failed),
        )
        self.assertIn("conversation messages: 3", render_rewind_preview(code_failed))

    def test_global_failure_conversation_is_unavailable(self) -> None:
        reason = RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW
        preview = build_rewind_preview(
            RewindKind.BOTH,
            make_facts(
                conversation_messages=0,
                code_paths=(),
                conversation_disabled_reason=reason,
                code_disabled_reason=reason,
            ),
        )
        self.assertIn("conversation messages: unavailable", render_rewind_preview(preview))
        self.assertIn(
            "state: disabled · source-changed-during-preview",
            render_rewind_preview(preview),
        )

    def test_path_limit_and_hidden_count_use_full_tuple(self) -> None:
        preview = build_rewind_preview(RewindKind.CODE, make_facts())
        rendered = render_rewind_preview(preview, max_path_rows=1)
        self.assertIn("code paths: 2", rendered)
        self.assertIn("pre-agent baselines preserved: 1", rendered)
        self.assertIn("... 1 paths hidden", rendered)
        self.assertNotIn("src/new.py", rendered)

    def test_untrusted_preview_text_is_single_line_and_control_safe(self) -> None:
        facts = make_facts(
            checkpoint_id="cp\x1b[2J\u2028forged",
            checkpoint_label="label\r\nstate: enabled\u2029tail",
            code_paths=(
                RewindPath(
                    "src/\u2028evil.py",
                    "base\x00\r\napply available",
                    True,
                ),
            ),
        )
        rendered = render_rewind_preview(
            build_rewind_preview(RewindKind.BOTH, facts)
        )
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\r", rendered)
        self.assertNotIn("\u2028", rendered)
        self.assertNotIn("\u2029", rendered)
        self.assertEqual(rendered.count("state:"), 2)
        self.assertEqual(rendered.count("\n"), len(rendered.splitlines()) - 1)

    def test_render_limits_fail_closed(self) -> None:
        preview = build_rewind_preview(RewindKind.BOTH, make_facts())
        for invalid in (True, 1.5, None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TypeError):
                    render_rewind_preview(preview, max_path_rows=invalid)
        for invalid in (-1, 21):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    render_rewind_preview(preview, max_path_rows=invalid)
        self.assertIn("... 2 paths hidden", render_rewind_preview(preview, max_path_rows=0))


if __name__ == "__main__":
    unittest.main()
