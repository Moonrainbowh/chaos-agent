from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import Message, ModelEvent, ModelEventKind  # noqa: E402
from code_agent.interfaces.controller import AgentController  # noqa: E402
from code_agent.interfaces.terminal_state import ApprovalBroker, TerminalState  # noqa: E402
from code_agent.interfaces.tests._support import FakeEngine  # noqa: E402
from code_agent.interfaces.windows_tui import (  # noqa: E402
    WindowsTerminalApp,
    render_terminal,
)


class WindowsTerminalRendererTests(unittest.TestCase):
    def test_renderer_exposes_transcript_timeline_diff_and_input_without_escape_sequences(self) -> None:
        state = TerminalState()
        state.thread_id = "thread-1"
        state.status = "running"
        state.transcript = ["user: inspect", "assistant: reading"]
        state.timeline = ["turn started", "requested read_file"]
        state.diff = "--- a/x.py\n+++ b/x.py\n+new"

        rendered = render_terminal(state, "next input", 80, 24, show_diff=True)

        self.assertTrue(rendered.startswith("\x1b[2J\x1b[H"))
        self.assertIn("code-agent", rendered)
        self.assertIn("assistant: reading", rendered)
        self.assertIn("requested read_file", rendered)
        self.assertIn("+++ b/x.py", rendered)
        self.assertIn("> next input", rendered)

    def test_renderer_shows_an_older_transcript_window_at_history_offset(self) -> None:
        state = TerminalState()
        state.summary = ["goal: recover"]
        state.timeline = ["requested read_file"]
        state.transcript = [f"line {index}" for index in range(6)]

        rendered = render_terminal(state, "", 80, 12, history_offset=2)

        self.assertIn("line 0", rendered)
        self.assertIn("line 3", rendered)
        self.assertNotIn("line 4", rendered)
        self.assertNotIn("line 5", rendered)
        self.assertIn("goal: recover", rendered)
        self.assertIn("recent: requested read_file", rendered)


class WindowsTerminalAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_consumes_engine_events_and_redraws(self) -> None:
        events = (
            AgentEvent(EventKind.RUN_STARTED, {"thread_id": "thread-1"}),
            AgentEvent(
                EventKind.MODEL_EVENT,
                {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="hello").to_dict()},
            ),
            AgentEvent(EventKind.COMPLETED, {"thread_id": "thread-1"}),
        )
        output: list[str] = []
        app = WindowsTerminalApp(
            AgentController(FakeEngine(events)),
            ApprovalBroker(),
            write=output.append,
        )

        self.assertTrue(await app.submit("inspect"))
        await app.wait_idle()

        self.assertEqual(app.state.thread_id, "thread-1")
        self.assertEqual(app.state.status, "completed")
        self.assertIn("assistant: hello", app.state.transcript)
        self.assertGreaterEqual(len(output), 3)
        self.assertFalse(await app.submit("   "))
        await asyncio.sleep(0)

    async def test_history_navigation_changes_and_resets_the_offset(self) -> None:
        app = WindowsTerminalApp(AgentController(FakeEngine(())), ApprovalBroker())
        app.state.transcript = [f"line {index}" for index in range(6)]

        await app.handle_key("page_up")

        self.assertGreater(app.history_offset, 0)
        await app.handle_key("end")
        self.assertEqual(app.history_offset, 0)

    async def test_restore_thread_replaces_state_from_history_reader(self) -> None:
        reader = _HistoryReader((Message("user", "resume task"), Message("assistant", "restored")))
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(), history=reader
        )

        restored = await app.restore_thread("thread-1")

        self.assertTrue(restored)
        self.assertEqual(app.current_thread_id, "thread-1")
        self.assertIn("goal: resume task", app.state.summary)
        self.assertIn("assistant: restored", app.state.transcript)

    async def test_restore_thread_failure_keeps_previous_state(self) -> None:
        app = WindowsTerminalApp(
            AgentController(FakeEngine(())), ApprovalBroker(), history=_FailingHistoryReader()
        )
        app.state.transcript = ["assistant: keep this"]

        restored = await app.restore_thread("thread-1")

        self.assertFalse(restored)
        self.assertEqual(app.state.transcript, ["assistant: keep this"])
        self.assertEqual(app.state.status, "session restore failed")


class _HistoryReader:
    def __init__(self, messages: tuple[Message, ...]) -> None:
        self.messages = messages

    async def load_messages(self, thread_id: str) -> tuple[Message, ...]:
        return self.messages

    async def load_events(self, thread_id: str) -> tuple[AgentEvent, ...]:
        return ()

    async def list_goals(self, thread_id: str) -> tuple[object, ...]:
        return ()

    async def list_checkpoints(self, thread_id: str) -> tuple[object, ...]:
        return ()


class _FailingHistoryReader(_HistoryReader):
    def __init__(self) -> None:
        super().__init__(())

    async def load_messages(self, thread_id: str) -> tuple[Message, ...]:
        raise RuntimeError("storage unavailable")


if __name__ == "__main__":
    unittest.main()
