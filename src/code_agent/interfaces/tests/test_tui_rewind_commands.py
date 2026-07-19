from __future__ import annotations

import unittest
from datetime import datetime, timezone

from code_agent.interfaces.rewind_models import (
    RewindAsOf,
    RewindCheckpointCandidate,
    RewindCheckpointPage,
    RewindKind,
    RewindPreview,
)
from code_agent.interfaces.terminal_display import DisplayKind
from code_agent.interfaces.tui_rewind_commands import handle_rewind_command


UTC = timezone.utc


def make_page() -> RewindCheckpointPage:
    candidate = RewindCheckpointCandidate(
        "cp-1", "Before edit", datetime(2025, 1, 1, tzinfo=UTC), True, True
    )
    return RewindCheckpointPage((candidate,), "next")


def make_preview(kind: RewindKind) -> RewindPreview:
    observed = RewindAsOf(
        1, 2, 3, 1, "a" * 64, datetime(2025, 1, 1, tzinfo=UTC)
    )
    return RewindPreview(
        kind, "cp-1", "Before edit", observed, 0, (), (), True, True, False, False
    )


class RecordingSource:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.result: object = make_page()
        self.error: Exception | None = None

    async def list_candidates(
        self, thread_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> object:
        self.calls.append(("list", thread_id, cursor, limit))
        if self.error is not None:
            raise self.error
        return self.result

    async def preview(
        self, thread_id: str, checkpoint_id: str, kind: RewindKind
    ) -> object:
        self.calls.append(("preview", thread_id, checkpoint_id, kind))
        if self.error is not None:
            raise self.error
        return self.result


class RewindCommandSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_forwards_absent_and_quoted_opaque_cursor(self) -> None:
        for instruction, cursor in (("list", None), ('列表 "opaque cursor"', "opaque cursor"), ('list ""', "")):
            with self.subTest(instruction=instruction):
                source = RecordingSource()
                result = await handle_rewind_command(source, "thread-1", instruction)
                self.assertEqual(source.calls, [("list", "thread-1", cursor, 20)])
                self.assertEqual(result.display_kind, DisplayKind.METADATA)
                self.assertIn("rewind · candidate only", result.text)
                self.assertTrue(result.handled)

    async def test_preview_forwards_exact_checkpoint_and_kind(self) -> None:
        for action in ("preview", "预览"):
            for kind in RewindKind:
                with self.subTest(action=action, kind=kind):
                    source = RecordingSource()
                    source.result = make_preview(kind)
                    instruction = f'{action} "checkpoint one" {kind.value}'
                    result = await handle_rewind_command(
                        source, "thread-1", instruction
                    )
                    self.assertEqual(
                        source.calls,
                        [("preview", "thread-1", "checkpoint one", kind)],
                    )
                    self.assertEqual(result.display_kind, DisplayKind.METADATA)
                    self.assertIn(f"kind: {kind.value}", result.text)


class RewindCommandErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_thread_is_required_without_source_call(self) -> None:
        for thread_id in (None, ""):
            with self.subTest(thread_id=thread_id):
                source = RecordingSource()
                result = await handle_rewind_command(source, thread_id, "list")
                self.assertEqual(result.text, "rewind requires a current thread")
                self.assertEqual(result.display_kind, DisplayKind.ERROR)
                self.assertEqual(source.calls, [])
                self.assertTrue(result.handled)

    async def test_general_syntax_errors_are_stable(self) -> None:
        expected = (
            "rewind expects list [cursor] or preview "
            "<checkpoint-id> <conversation|code|both>"
        )
        for instruction in (None, "", "apply cp-1", 'preview "unterminated'):
            with self.subTest(instruction=instruction):
                result = await handle_rewind_command(
                    RecordingSource(), "thread-1", instruction
                )
                self.assertEqual(result.text, expected)
                self.assertEqual(result.display_kind, DisplayKind.ERROR)

    async def test_action_arity_and_kind_errors_are_specific(self) -> None:
        cases = (
            ("list one two", "rewind list expects at most one cursor"),
            (
                "preview cp-1",
                "rewind preview expects <checkpoint-id> <conversation|code|both>",
            ),
            (
                "preview cp-1 BOTH",
                "rewind kind must be conversation, code, or both",
            ),
        )
        for instruction, expected in cases:
            with self.subTest(instruction=instruction):
                result = await handle_rewind_command(
                    RecordingSource(), "thread-1", instruction
                )
                self.assertEqual(result.text, expected)

    async def test_unavailable_invalid_and_failed_sources_are_sanitized(self) -> None:
        sources = (None, RecordingSource(), RecordingSource())
        sources[1].result = object()
        sources[2].error = RuntimeError("secret provider detail")
        for source in sources:
            with self.subTest(source=source):
                result = await handle_rewind_command(
                    source, "thread-1", "list"
                )
                self.assertEqual(result.text, "rewind source is unavailable")
                self.assertEqual(result.display_kind, DisplayKind.ERROR)
                self.assertNotIn("RuntimeError", result.text)
                self.assertNotIn("secret", result.text)


if __name__ == "__main__":
    unittest.main()
