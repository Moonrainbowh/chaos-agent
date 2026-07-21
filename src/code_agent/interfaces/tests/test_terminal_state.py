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


def _text_delta(text: str) -> AgentEvent:
    return AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.TEXT_DELTA, text=text).to_dict()})


class TerminalStateTests(unittest.TestCase):
    def test_context_and_model_lifecycle_have_distinct_running_phases(self) -> None:
        state = TerminalState()
        state.apply(AgentEvent(EventKind.RUN_STARTED, {}))
        state.apply(AgentEvent(EventKind.TURN_STARTED, {}))
        self.assertEqual(state.status, "building_context")
        state.apply(AgentEvent(EventKind.CONTEXT_BUILT, {}))
        self.assertEqual(state.status, "waiting_model")
        state.apply(AgentEvent(EventKind.MODEL_STARTED, {}))
        self.assertEqual(state.status, "waiting_model")
        state.apply(_text_delta("first token"))
        self.assertEqual(state.status, "running")

    def test_action_request_takes_precedence_over_waiting_for_model(self) -> None:
        state = TerminalState()
        state.apply(AgentEvent(EventKind.MODEL_STARTED, {}))

        state.apply(
            AgentEvent(
                EventKind.ACTION_REQUESTED,
                {
                    "request": {
                        "id": "call-1",
                        "name": "read_file",
                        "arguments": {"path": "src/a.py"},
                    }
                },
            )
        )

        self.assertEqual(state.status, "running")
        self.assertEqual(state.active_action, "read_file")

    def test_user_pause_cancellation_is_not_presented_as_an_error(self) -> None:
        state = TerminalState()
        state.begin_run()
        state.apply(AgentEvent(EventKind.CANCELLED, {"reason": "user requested pause"}))
        self.assertEqual(state.status, "paused")

    def test_state_tracks_transcript_timeline_status_and_diff(self) -> None:
        state = TerminalState()
        state.apply(AgentEvent(EventKind.RUN_STARTED, {"thread_id": "thread-1"}))
        state.apply(_text_delta("hello"))
        state.apply(
            AgentEvent(
                EventKind.ACTION_REQUESTED,
                {"request": {"id": "call-1", "name": "write_file", "arguments": {"diff": "--- a/x\n+++ b/x"}}},
            )
        )
        state.apply(AgentEvent(EventKind.ACTION_COMPLETED, {"result": ActionResult("call-1", "write_file", {}).to_dict()}))
        state.apply(_text_delta("done"))
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
        state.apply(_text_delta("hello "))
        state.apply(_text_delta("world"))
        state.apply(AgentEvent(EventKind.COMPLETED, {}))
        self.assertEqual(state.transcript, ["assistant: hello world"])

    def test_text_delta_is_visible_as_draft_before_final_message(self) -> None:
        state = TerminalState()
        state.apply(_text_delta("hello "))
        state.apply(_text_delta("world"))
        self.assertEqual(state.draft_answer, "hello world")
        self.assertTrue(state.has_draft)
        self.assertEqual(state.entries, [])

    def test_completed_message_clears_draft_and_adds_one_final_entry(self) -> None:
        state = TerminalState()
        state.apply(_text_delta("answer"))
        message = Message(role="assistant", content="answer")
        state.apply(AgentEvent(EventKind.MESSAGE_ADDED, {"message": message.to_dict()}))
        state.apply(AgentEvent(EventKind.COMPLETED, {}))
        self.assertEqual(state.draft_answer, "")
        self.assertEqual([entry.text for entry in state.entries], ["answer"])

    def test_cancelled_draft_becomes_non_conversation_partial_entry(self) -> None:
        state = TerminalState()
        state.apply(_text_delta("unfinished"))
        state.apply(AgentEvent(EventKind.CANCELLED, {"reason": "user requested pause"}))
        self.assertEqual(state.entries[-1].kind, DisplayKind.PARTIAL_AGENT)
        self.assertEqual(state.entries[-1].text, "unfinished")
        self.assertEqual(state.transcript, [])
        self.assertFalse(state.has_draft)

    def test_error_draft_becomes_non_conversation_partial_entry(self) -> None:
        state = TerminalState()
        state.apply(_text_delta("interrupted"))
        state.apply(AgentEvent(EventKind.ERROR, {}))
        self.assertEqual(state.entries[-1].kind, DisplayKind.PARTIAL_AGENT)
        self.assertEqual(state.entries[-1].text, "interrupted")
        self.assertEqual(state.transcript, [])
        self.assertFalse(state.has_draft)

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
        self.assertEqual(state.entries[-1].text, "read_file")

    def test_action_summary_keeps_only_request_facts(self) -> None:
        state = TerminalState()
        state.apply(AgentEvent(EventKind.ACTION_REQUESTED, {"request": {"id": "call-1", "name": "read_file", "arguments": {"path": "src/a.py", "secret": "nope"}}}))
        state.apply(AgentEvent(EventKind.ACTION_COMPLETED, {"result": ActionResult("call-1", "read_file", {"content": "unbounded"}, metadata={"lines": 7, "duration_ms": 12}).to_dict()}))

        self.assertIn("path: src/a.py", state.entries[-1].text)
        self.assertIn("lines: 7", state.entries[-1].text)
        self.assertNotIn("unbounded", state.entries[-1].text)
        self.assertNotIn("nope", state.entries[-1].text)

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
