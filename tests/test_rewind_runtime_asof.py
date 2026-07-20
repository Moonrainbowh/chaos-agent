from __future__ import annotations
import asyncio
import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import patch
from code_agent.interfaces.rewind_models import (
    RewindDisabledReason, RewindFacts, RewindKind,
)
from code_agent.interfaces.rewind_view import build_rewind_preview
from code_agent.sessions.errors import (
    SessionCorruptionError, SessionNotFound, SessionStorageError,
)
from code_agent.sessions.rewind_models import RewindCoverageState
from code_agent.sessions.rewind_models import (
    CoverageToken, RewindCheckpointAnchor,
)
from code_agent.workspace.errors import FileTooLargeError
from code_agent.workspace.errors import WorkspaceError
from code_agent.workspace.rewind_state import relevant_path_digest
from tests.test_rewind_runtime import (
    NOW, FakeSessions, RuntimeHarness, checkpoint, heads, observation,
)
from tests.test_rewind_runtime_integrity import IntegrityHarness
from code_agent_win._rewind_runtime_validation import CodeProjection
LATER = datetime(2026, 7, 20, tzinfo=timezone.utc)
def _assert_disabled_facts(test, facts, item, conversation_reason, code_reason):
    code_head = (
        item.checkpoint_fact is not None
        and item.checkpoint_fact.workspace_fingerprint == test.fingerprint)
    test.assertEqual(
        (
            facts.checkpoint_id, facts.checkpoint_label, facts.checkpoint_created_at,
            facts.conversation_messages, facts.code_paths,
            facts.conversation_disabled_reason, facts.code_disabled_reason,
            facts.as_of.message_sequence, facts.as_of.event_sequence,
            facts.as_of.mutation_sequence, facts.as_of.coverage_generation,
            facts.as_of.relevant_path_digest, facts.as_of.captured_at,
        ),
        (
            "cp", "checkpoint", NOW, 0, (), conversation_reason, code_reason,
            item.heads.message_sequence, item.heads.event_sequence,
            item.heads.mutation_sequence if code_head else None,
            item.heads.coverage_generation if code_head else None,
            relevant_path_digest(()) if code_reason is None else None, NOW),
    )
class RewindRuntimeAsOfTests(
    RuntimeHarness, unittest.IsolatedAsyncioTestCase
):
    async def test_checkpoint_fact_move_with_same_heads_retries(self):
        legacy = observation(
            self.fingerprint, anchor=None,
            head=heads(generation=None, state=None))
        current = observation(self.fingerprint)
        runtime = self.runtime((legacy, current, legacy, current))
        preview = await runtime.preview("thread", "cp", RewindKind.CODE)
        self.assertEqual(preview.disabled_reasons, (
            RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW,))
        self.assertEqual(sum(c[0] == "observe" for c in self.sessions.calls), 4)
    async def test_second_current_state_overflow_maps_preview_limit(self):
        item = observation(self.fingerprint)
        with patch(
            "code_agent_win.rewind_runtime.observe_file_states",
            side_effect=FileTooLargeError("second observation too large"),
        ):
            preview = await self.runtime((item, item)).preview(
                "thread", "cp", RewindKind.CODE
            )
        self.assertEqual(preview.disabled_reasons, (
            RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED,))
    async def test_successful_anchor_with_moved_sequence_retries_then_falls_back(self):
        item = observation(
            self.fingerprint, head=heads(mutations=2), limited=True
        )
        runtime = self.runtime((item, item, item, item))
        wrong_sequence = RewindCheckpointAnchor(
            CoverageToken(self.fingerprint, 1), "thread",
            RewindCoverageState.ACTIVE, 1,
        )
        wrong_state = RewindCheckpointAnchor(
            CoverageToken(self.fingerprint, 1), "thread",
            RewindCoverageState.INVALIDATED, 2,
        )
        self.sessions.anchor_outcomes = [wrong_sequence, wrong_state]
        preview = await runtime.preview("thread", "cp", RewindKind.CODE)
        self.assertEqual(preview.disabled_reasons, (
            RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW,))
    async def test_two_moves_become_empty_mirrored_source_changed(self):
        values = tuple(
            observation(self.fingerprint, head=heads(messages=value), count=value)
            for value in (1, 2, 3, 4)
        )
        captured, calls = [], []
        runtime = self.runtime(values)
        runtime.clock = lambda: calls.append(LATER) or LATER
        with patch(
            "code_agent_win.rewind_runtime.build_rewind_preview",
            side_effect=lambda kind, facts: (
                captured.append(facts) or build_rewind_preview(kind, facts)),
        ):
            preview = await runtime.preview("thread", "cp", RewindKind.BOTH)
        self.assertEqual(preview.disabled_reasons, (
            RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW,
        ))
        self.assertEqual(preview.checkpoint_label,
                         "checkpoint changed during preview")
        self.assertEqual(
            (preview.as_of.message_sequence, preview.as_of.event_sequence,
             preview.as_of.mutation_sequence, preview.as_of.coverage_generation,
             preview.as_of.relevant_path_digest),
            (None, None, None, None, None),
        )
        facts = captured[-1]
        reason = RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW
        self.assertEqual((facts.conversation_disabled_reason,
                          facts.code_disabled_reason), (reason, reason))
        self.assertEqual((facts.checkpoint_created_at, facts.as_of.captured_at),
                         (LATER, LATER))
        self.assertEqual(calls, [LATER])
    async def test_legacy_conversation_ignores_workspace_head_changes(self):
        first = observation(
            self.fingerprint, anchor=None, count=1,
            head=heads(messages=1, generation=None, state=None),
        )
        second = observation(
            self.fingerprint, anchor=None, count=1,
            head=heads(messages=1, mutations=9, generation=None, state=None),
        )
        preview = await self.runtime((first, second)).preview(
            "thread", "cp", RewindKind.CONVERSATION
        )
        self.assertTrue(preview.enabled)
    async def test_conversation_only_performs_no_snapshot_load(self):
        item = observation(self.fingerprint, count=1, head=heads(messages=1))
        with patch.object(
            self.snapshots, "load", side_effect=AssertionError("snapshot loaded")
        ):
            preview = await self.runtime((item, item)).preview(
                "thread", "cp", RewindKind.CONVERSATION
            )
        self.assertTrue(preview.enabled)

    async def test_stable_asof_fields_and_exact_success_facts(self):
        item = observation(
            self.fingerprint, count=3,
            head=heads(messages=5, events=7, mutations=0, generation=1),
        )
        captured: list[RewindFacts] = []

        def build(kind, facts):
            captured.append(facts)
            return build_rewind_preview(kind, facts)

        with patch("code_agent_win.rewind_runtime.build_rewind_preview",
                   side_effect=build):
            preview = await self.runtime((item, item)).preview(
                "thread", "cp", RewindKind.BOTH
            )
        facts = captured[-1]
        self.assertEqual(
            (facts.checkpoint_id, facts.checkpoint_label,
             facts.checkpoint_created_at, facts.conversation_messages,
             facts.code_paths, facts.conversation_disabled_reason,
             facts.code_disabled_reason),
            ("cp", "checkpoint", NOW, 3, (), None, None),
        )
        self.assertEqual(
            (preview.as_of.message_sequence, preview.as_of.event_sequence,
             preview.as_of.mutation_sequence, preview.as_of.coverage_generation,
             preview.as_of.captured_at),
            (5, 7, 0, 1, NOW),
        )
    async def test_each_disabled_facet_maps_exact_facts_and_no_code_digest(self):
        base = observation(self.fingerprint)
        cases = (
            (observation(self.fingerprint, cp=checkpoint(bound=None), count=None),
             RewindDisabledReason.MESSAGE_BOUND_MISSING, None, None),
            (observation(self.fingerprint, cp=checkpoint(bound=2), count=None,
                         head=heads(messages=1)),
             RewindDisabledReason.MESSAGE_BOUND_INVALID, None, None),
            (observation(self.fingerprint, anchor=None,
                         head=heads(generation=None, state=None)), None,
             RewindDisabledReason.CODE_COVERAGE_UNAVAILABLE, None),
            (observation(self.fingerprint,
                         head=heads(state=RewindCoverageState.INVALIDATED)), None,
             RewindDisabledReason.CODE_JOURNAL_INCOMPLETE, None),
        ) + tuple(
            (base, None, reason, reason) for reason in (
                RewindDisabledReason.PENDING_WORKSPACE_MUTATION,
                RewindDisabledReason.SNAPSHOT_MISSING,
                RewindDisabledReason.SNAPSHOT_INVALID,
                RewindDisabledReason.WORKSPACE_CONFLICT,
                RewindDisabledReason.PREVIEW_LIMIT_EXCEEDED,
            )
        )
        for item, conversation_reason, code_reason, forced in cases:
            with self.subTest(code_reason=code_reason):
                captured = []
                projection = (
                    patch("code_agent_win.rewind_runtime.project_code",
                          return_value=CodeProjection((), (), None, forced))
                    if forced else nullcontext()
                )
                with projection, patch(
                    "code_agent_win.rewind_runtime.build_rewind_preview",
                    side_effect=lambda kind, facts: (
                        captured.append(facts) or build_rewind_preview(kind, facts)
                    ),
                ):
                    await self.runtime((item, item)).preview(
                        "thread", "cp", RewindKind.BOTH
                    )
                _assert_disabled_facts(
                    self, captured[-1], item, conversation_reason, code_reason
                )

    async def test_not_found_uses_one_fallback_clock_value(self):
        calls = []

        def clock():
            calls.append(LATER)
            return LATER

        runtime = self.runtime()
        runtime.clock = clock
        captured = []
        with patch(
            "code_agent_win.rewind_runtime.build_rewind_preview",
            side_effect=lambda kind, facts: (
                captured.append(facts) or build_rewind_preview(kind, facts)),
        ):
            preview = await runtime.preview("thread", "missing", RewindKind.BOTH)
        self.assertEqual(preview.disabled_reasons, (
            RewindDisabledReason.CHECKPOINT_NOT_FOUND,
        ))
        self.assertEqual(preview.as_of.captured_at, LATER)
        self.assertEqual(calls, [LATER])
        facts = captured[-1]
        reason = RewindDisabledReason.CHECKPOINT_NOT_FOUND
        self.assertEqual((facts.conversation_disabled_reason,
                          facts.code_disabled_reason), (reason, reason))

    async def test_sessions_corruption_cancellation_and_io_propagate(self):
        errors = (
            SessionCorruptionError("corrupt"),
            asyncio.CancelledError(),
            SessionStorageError("io"),
            OSError("unexpected"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                sessions = FakeSessions((error,))
                runtime = self.runtime((observation(self.fingerprint),))
                runtime.sessions = sessions
                with self.assertRaises(type(error)):
                    await runtime.preview("thread", "cp", RewindKind.BOTH)

    async def test_preview_does_not_save_apply_or_restore(self):
        item = observation(self.fingerprint)
        runtime = self.runtime((item, item))
        with (
            patch.object(self.snapshots, "save") as save,
            patch.object(self.editor, "apply") as apply,
            patch.object(self.editor, "restore") as restore,
        ):
            preview = await runtime.preview("thread", "cp", RewindKind.BOTH)
        self.assertTrue(preview.enabled)
        save.assert_not_called()
        apply.assert_not_called()
        restore.assert_not_called()

    async def test_limit_probe_non_storage_errors_propagate(self):
        item = observation(
            self.fingerprint, head=heads(mutations=2), limited=True)
        errors = (
            SessionNotFound("anchor"), SessionCorruptionError("corrupt"),
            asyncio.CancelledError(), OSError("unexpected"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                runtime = self.runtime((item, item))
                self.sessions.anchor_outcomes = [error]
                with self.assertRaises(type(error)):
                    await runtime.preview("thread", "cp", RewindKind.CODE)


class UnexpectedSnapshotTests(IntegrityHarness, unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_snapshot_and_workspace_errors_propagate(self):
        (self.workspace / "a.txt").write_bytes(b"after")
        record = self.completed("a.txt", b"before", b"after")
        for error in (OSError("io"), WorkspaceError("unexpected")):
            with self.subTest(error=type(error).__name__), patch.object(
                self.snapshots, "load", side_effect=error
            ), self.assertRaises(type(error)):
                await self.preview_records((record,))


if __name__ == "__main__":
    unittest.main()
