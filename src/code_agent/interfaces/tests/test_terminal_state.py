from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationError, CancellationToken  # noqa: E402
from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import ModelEvent, ModelEventKind  # noqa: E402
from code_agent.interfaces.terminal_state import (  # noqa: E402
    ApprovalBroker,
    ApprovalRequest,
    TerminalState,
)


class TerminalStateTests(unittest.TestCase):
    def test_state_tracks_transcript_timeline_status_and_diff(self) -> None:
        state = TerminalState()
        state.apply(AgentEvent(EventKind.RUN_STARTED, {"thread_id": "thread-1"}))
        state.apply(
            AgentEvent(
                EventKind.MODEL_EVENT,
                {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="hello").to_dict()},
            )
        )
        state.apply(
            AgentEvent(
                EventKind.ACTION_REQUESTED,
                {"request": {"id": "call-1", "name": "write_file", "arguments": {"diff": "--- a/x\n+++ b/x"}}},
            )
        )
        state.apply(AgentEvent(EventKind.COMPLETED, {"thread_id": "thread-1"}))

        self.assertEqual(state.thread_id, "thread-1")
        self.assertEqual(state.transcript, ["assistant: hello"])
        self.assertIn("write_file", state.timeline[-2])
        self.assertEqual(state.diff, "--- a/x\n+++ b/x")
        self.assertEqual(state.status, "completed")


class ApprovalBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_waits_for_matching_keyboard_decision(self) -> None:
        broker = ApprovalBroker()
        request = ApprovalRequest("call-1", "write_file", {"path": "x.py"})
        waiting = asyncio.create_task(broker.request(request, CancellationToken()))

        pending = await broker.next_request()
        self.assertEqual(pending, request)
        self.assertTrue(broker.resolve("call-1", True))
        self.assertTrue(await waiting)
        self.assertFalse(broker.resolve("call-1", False))

    async def test_cancellation_unblocks_an_unanswered_request(self) -> None:
        broker = ApprovalBroker()
        token = CancellationToken()
        waiting = asyncio.create_task(
            broker.request(ApprovalRequest("call-1", "run_command", {}), token)
        )
        await broker.next_request()
        token.cancel("user cancelled")

        with self.assertRaises(CancellationError):
            await waiting


if __name__ == "__main__":
    unittest.main()
