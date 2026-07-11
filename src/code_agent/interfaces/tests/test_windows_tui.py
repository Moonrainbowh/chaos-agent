from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import ModelEvent, ModelEventKind  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
