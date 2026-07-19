from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.interfaces.rewind_models import (  # noqa: E402
    RewindAsOf,
    RewindCheckpointCandidate,
    RewindCheckpointPage,
    RewindDisabledReason,
    RewindFacts,
    RewindKind,
    RewindPath,
)
from code_agent.interfaces.tui_commands import (  # noqa: E402
    ParseOutcome,
    TuiCommand,
    TuiCommandKind,
)
from code_agent.interfaces.windows_tui import WindowsTerminalApp  # noqa: E402
from code_agent.interfaces.rewind_view import build_rewind_preview  # noqa: E402
from code_agent_win.app_ui import ModeAwareWindowsTerminalApp  # noqa: E402


NOW = datetime(2026, 7, 19, 10, tzinfo=timezone.utc)


class _Capability:
    def lines(self) -> tuple[str, ...]:
        return ()


def _preview(kind: RewindKind, *, disabled: bool = False):
    reason = RewindDisabledReason.WORKSPACE_CONFLICT if disabled else None
    facts = RewindFacts(
        checkpoint_id="cp-1", checkpoint_label="before edit",
        checkpoint_created_at=NOW,
        as_of=RewindAsOf(2, 3, 4, 1, "a" * 64, NOW),
        conversation_messages=3,
        code_paths=() if disabled else (RewindPath("note.txt", "existing", True),),
        conversation_disabled_reason=None, code_disabled_reason=reason,
    )
    return build_rewind_preview(kind, facts)


class FakeRewindSource:
    def __init__(self, *, disabled: bool = False) -> None:
        self.disabled = disabled
        self.calls: list[tuple[object, ...]] = []

    async def list_candidates(self, thread_id, *, cursor=None, limit=20):
        self.calls.append(("list", thread_id, cursor, limit))
        items = tuple(
            RewindCheckpointCandidate(
                f"cp-{index:02d}", f"candidate {index}", NOW, True, True
            )
            for index in range(21)
        )
        return RewindCheckpointPage(items, "next-opaque")

    async def preview(self, thread_id, checkpoint_id, kind):
        self.calls.append(("preview", thread_id, checkpoint_id, kind))
        return _preview(kind, disabled=self.disabled)


def _app(rewind: object | None) -> ModeAwareWindowsTerminalApp:
    return ModeAwareWindowsTerminalApp(
        object(), object(), capability=_Capability(), rewind=rewind,
        write=lambda value: None,
    )


class RewindTuiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rewind_command_is_visible_only_with_runtime(self) -> None:
        missing = _app(None)
        present = _app(FakeRewindSource())
        self.assertFalse(await missing.submit("/rewind list"))
        self.assertIn("unknown or unavailable", missing.state.transcript[-1])
        present.current_thread_id = "thread-1"
        self.assertTrue(await present.submit("/rewind list"))

    async def test_list_is_paginated_and_says_candidate_only(self) -> None:
        source, app = FakeRewindSource(), _app(None)
        app.rewind = source
        app.current_thread_id = "thread-1"
        self.assertTrue(await app.submit("/rewind list opaque"))
        self.assertEqual(source.calls, [("list", "thread-1", "opaque", 20)])
        rendered = app.state.transcript[-1]
        self.assertIn("candidate only", rendered)
        self.assertIn("... 1 candidates hidden", rendered)
        self.assertIn("cp-19", rendered)
        self.assertNotIn("cp-20", rendered)
        self.assertIn("opaque next cursor: next-opaque", rendered)

    async def test_conversation_code_and_both_previews_render(self) -> None:
        expected = {
            RewindKind.CONVERSATION: ("conversation messages: 3", "code paths: 0", False),
            RewindKind.CODE: ("conversation messages: unavailable", "code paths: 1", True),
            RewindKind.BOTH: ("conversation messages: 3", "code paths: 1", True),
        }
        for kind in RewindKind:
            with self.subTest(kind=kind):
                source, app = FakeRewindSource(), _app(None)
                app.rewind, app.current_thread_id = source, "thread-1"
                handled = await app.submit(f"/rewind preview cp-1 {kind.value}")
                conversation, paths, has_note = expected[kind]
                rendered = app.state.transcript[-1]
                self.assertTrue(handled)
                self.assertIn(f"kind: {kind.value}", rendered)
                self.assertIn(conversation, rendered)
                self.assertIn(paths, rendered)
                self.assertEqual("note.txt" in rendered, has_note)
                self.assertEqual(source.calls[-1][-1], kind)

    async def test_disabled_reason_value_is_stable(self) -> None:
        app = _app(FakeRewindSource(disabled=True))
        app.current_thread_id = "thread-1"
        await app.submit("/rewind preview cp-1 code")
        self.assertIn("workspace-conflict", app.state.transcript[-1])

    async def test_footer_says_preview_only_apply_unavailable_and_no_git_reset(self) -> None:
        app = _app(FakeRewindSource())
        app.current_thread_id = "thread-1"
        await app.submit("/rewind preview cp-1 both")
        rendered = app.state.transcript[-1]
        for phrase in ("preview only", "apply unavailable", "no git reset"):
            self.assertIn(phrase, rendered)

    async def test_command_calls_no_provider_action_approval_or_checkpoint(self) -> None:
        class Trap:
            def __getattr__(self, name):
                raise AssertionError(f"unexpected side effect: {name}")

        source, app = FakeRewindSource(), _app(None)
        app.rewind, app.current_thread_id = source, "thread-1"
        app.controller = app.approvals = app.sessions = Trap()
        self.assertTrue(await app.submit("/rewind preview cp-1 code"))
        self.assertEqual(len(source.calls), 1)

    async def test_state_thread_id_fallback_allows_foreground_rewind(self) -> None:
        source, app = FakeRewindSource(), _app(None)
        app.rewind, app.state.thread_id = source, "foreground-thread"
        self.assertTrue(await app.submit("/rewind list"))
        self.assertEqual(source.calls[0][1], "foreground-thread")

    async def test_non_rewind_commands_delegate_to_base_handler(self) -> None:
        app = _app(FakeRewindSource())
        outcome = ParseOutcome(TuiCommand(TuiCommandKind.STATUS))
        with patch.object(
            WindowsTerminalApp, "_handle_command", new=AsyncMock(return_value=False)
        ) as delegated:
            self.assertFalse(await app._handle_command(outcome))
        delegated.assert_awaited_once_with(outcome)


if __name__ == "__main__":
    unittest.main()
