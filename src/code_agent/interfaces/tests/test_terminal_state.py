from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationError, CancellationToken  # noqa: E402
from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import (  # noqa: E402
    ActionResult,
    Message,
    ModelEvent,
    ModelEventKind,
)
from code_agent.interfaces.history import RestoredThread  # noqa: E402
from code_agent.interfaces.terminal_display import DisplayKind  # noqa: E402
from code_agent.interfaces.terminal_state import (  # noqa: E402
    ApprovalBroker,
    ApprovalRequest,
    TerminalState,
)
from code_agent.sessions.models import (  # noqa: E402
    CheckpointRecord,
    GoalRecord,
    GoalStatus,
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
        state.apply(AgentEvent(EventKind.ACTION_COMPLETED, {"result": ActionResult("call-1", "write_file", {}).to_dict()}))
        state.apply(AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="done").to_dict()}))
        state.apply(AgentEvent(EventKind.COMPLETED, {"thread_id": "thread-1"}))

        self.assertEqual(state.thread_id, "thread-1")
        self.assertEqual(state.transcript, ["assistant: done"])
        self.assertTrue(any("write_file" in line for line in state.timeline))
        self.assertEqual(state.diff, "--- a/x\n+++ b/x")
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.execution_summary, "已完成 1 项操作")

    def test_restore_projects_persisted_thread_state(self) -> None:
        state = TerminalState()
        history = RestoredThread(
            thread_id="thread-1",
            messages=(
                Message(role="user", content="inspect"),
                Message(role="assistant", content="done"),
                Message(role="tool", name="read_file", content="raw output"),
            ),
            events=(
                AgentEvent(
                    EventKind.ACTION_REQUESTED,
                    {
                        "request": {
                            "id": "call-1",
                            "name": "read_file",
                            "arguments": {"diff": "--- a/x\n+++ b/x"},
                        }
                    },
                ),
                AgentEvent(
                    EventKind.ACTION_COMPLETED,
                    {
                        "result": ActionResult(
                            request_id="call-1",
                            name="read_file",
                            output={"content": "x"},
                        ).to_dict()
                    },
                ),
                AgentEvent(EventKind.COMPLETED, {"thread_id": "thread-1"}),
            ),
            goals=(
                GoalRecord(
                    id="goal-1",
                    thread_id="thread-1",
                    objective="inspect repository",
                    status=GoalStatus.ACTIVE,
                ),
            ),
            checkpoints=(
                CheckpointRecord(
                    id="checkpoint-1",
                    thread_id="thread-1",
                    label="before edits",
                    created_at=datetime(2026, 7, 11, tzinfo=timezone.utc),
                ),
            ),
        )

        state.restore(history)

        self.assertEqual(state.thread_id, "thread-1")
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.summary[0], "goal: inspect repository")
        self.assertEqual(state.transcript, ["user: inspect", "assistant: done"])
        self.assertEqual(
            state.timeline[-2:], ["requested read_file", "completed read_file"]
        )

    def test_restore_uses_first_user_message_when_no_goal_exists(self) -> None:
        state = TerminalState()
        history = RestoredThread(
            thread_id="thread-1",
            messages=(Message(role="user", content="recover this task"),),
            events=(),
            goals=(),
            checkpoints=(),
        )

        state.restore(history)

        self.assertEqual(state.summary[0], "goal: recover this task")

    def test_adjacent_text_deltas_become_one_final_answer(self) -> None:
        state = TerminalState()

        state.apply(
            AgentEvent(
                EventKind.MODEL_EVENT,
                {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="hello ").to_dict()},
            )
        )
        state.apply(
            AgentEvent(
                EventKind.MODEL_EVENT,
                {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="world").to_dict()},
            )
        )

        state.apply(AgentEvent(EventKind.COMPLETED, {}))
        self.assertEqual(state.transcript, ["assistant: hello world"])

    def test_persisted_final_message_is_visible_before_task_completion(self) -> None:
        state = TerminalState()
        state.apply(AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text="answer").to_dict()}))

        state.apply(AgentEvent(EventKind.MESSAGE_ADDED, {"message": Message(role="assistant", content="answer").to_dict()}))
        state.apply(AgentEvent(EventKind.TASK_STATUS_CHANGED, {"task_id": "task-1", "status": "verifying"}))

        self.assertEqual(state.transcript, ["assistant: answer"])
        self.assertEqual(state.entries[-1].text, "answer")
        state.apply(AgentEvent(EventKind.COMPLETED, {}))
        self.assertEqual(state.transcript, ["assistant: answer"])

    def test_begin_run_does_not_keep_the_previous_action_summary(self) -> None:
        state = TerminalState()
        state.execution_summary = "已完成 3 项操作"
        state.active_action = "write_file"

        state.begin_run()

        self.assertEqual(state.status, "running")
        self.assertEqual(state.execution_summary, "")
        self.assertIsNone(state.active_action)

    def test_completed_action_adds_a_compact_static_result_entry(self) -> None:
        state = TerminalState()
        state.apply(AgentEvent(EventKind.ACTION_REQUESTED, {"request": {"id": "call-1", "name": "read_file", "arguments": {}}}))
        state.apply(AgentEvent(EventKind.ACTION_COMPLETED, {"result": ActionResult("call-1", "read_file", {}).to_dict()}))

        self.assertEqual(state.entries[-1].kind, DisplayKind.TOOL)
        self.assertEqual(state.entries[-1].text, "read_file completed")

    def test_reasoning_delta_is_not_retained_or_rendered(self) -> None:
        state = TerminalState()

        for model_event in (
            ModelEvent(ModelEventKind.TEXT_DELTA, text="hello "),
            ModelEvent(ModelEventKind.TEXT_DELTA, text="world"),
            ModelEvent(ModelEventKind.REASONING_DELTA, text="planning"),
            ModelEvent(ModelEventKind.TEXT_DELTA, text="again"),
        ):
            state.apply(
                AgentEvent(EventKind.MODEL_EVENT, {"event": model_event.to_dict()})
            )

        state.apply(AgentEvent(EventKind.COMPLETED, {}))
        self.assertEqual(state.transcript, ["assistant: hello worldagain"])
        self.assertNotIn("planning", "\n".join(entry.text for entry in state.entries))


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
