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
    ToolCall,
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


def _restored_thread() -> RestoredThread:
    return RestoredThread(
        thread_id="thread-1",
        messages=(Message(role="user", content="inspect"), Message(role="assistant", content="done"), Message(role="tool", name="read_file", content="raw output")),
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


class TerminalStateTests(unittest.TestCase):
    def test_error_after_completion_does_not_keep_completed_delivery(self):
        from code_agent.interfaces.experience_summary import build_experience_snapshot
        state = TerminalState()
        state.apply(AgentEvent(EventKind.COMPLETED))
        state.apply(AgentEvent(EventKind.ERROR))
        self.assertEqual(build_experience_snapshot(state).status, 'failed')

    def test_old_completion_carries_unknown_verification(self):
        from code_agent.interfaces.experience_summary import build_experience_snapshot
        for event in (AgentEvent(EventKind.COMPLETED), AgentEvent(
                EventKind.TASK_STATUS_CHANGED, {'status': 'completed'})):
            with self.subTest(event=event.kind):
                state = TerminalState()
                state.apply(event)
                self.assertEqual(state.result.verification_status, 'unknown')
                self.assertFalse(build_experience_snapshot(state).can_claim_completion)

    def test_tool_result_does_not_replace_task_delivery(self):
        state = TerminalState()
        state.begin_run()
        state.apply(AgentEvent(EventKind.ACTION_COMPLETED,
            {'result': ActionResult('call', 'read_file', 'source').to_dict()}))
        self.assertEqual(state.status, 'running')
        self.assertIsNone(state.result)

    def test_cancelled_delivery_survives_late_completion_until_new_run(self):
        from code_agent.core.task_result import TaskResult
        state = TerminalState()
        state.begin_run()
        state.apply(AgentEvent(EventKind.CANCELLED, {'reason': 'user cancelled'}))
        state.apply(AgentEvent(EventKind.COMPLETED,
            {'result': TaskResult('completed', verification_status='verified').to_dict()}))
        self.assertEqual(state.status, 'cancelled')
        self.assertEqual(state.result.execution_status, 'cancelled')
        self.assertNotEqual(state.result.verification_status, 'verified')
        state.apply(AgentEvent(EventKind.RUN_STARTED, {}))
        state.apply(AgentEvent(EventKind.COMPLETED,
            {'result': TaskResult('completed', verification_status='verified').to_dict()}))
        self.assertEqual(state.status, 'completed')
        self.assertEqual(state.result.verification_status, 'verified')
        state.apply(AgentEvent(EventKind.CANCELLED, {'reason': 'user cancelled'}))
        state.apply(AgentEvent(EventKind.TASK_STATUS_CHANGED,
            {'status': 'running', 'run_instance_id': 'new-durable-run'}))
        self.assertEqual(state.status, 'running')
        self.assertIsNone(state.result)

    def test_settled_task_replaces_exploration_warning_with_durable_reason(self):
        state = TerminalState()
        state.apply(AgentEvent(EventKind.TASK_BUDGET_WARNING, {
            "category": "exploration", "reason": "task produced no answer text",
        }))
        state.apply(AgentEvent(EventKind.TASK_STATUS_CHANGED, {
            "status": "waiting_decision", "reason": "no workspace file changes",
        }))
        self.assertIsNone(state.task_budget_line)
        self.assertEqual(state.task_stop_reason, "no workspace file changes")

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
        self.assertEqual(state.status, "streaming_response")

    def test_phase_timing_accumulates_only_known_non_negative_durations(self) -> None:
        state = TerminalState()
        state.apply(AgentEvent(EventKind.RUN_STARTED, {}))
        state.apply(AgentEvent(EventKind.PHASE_COMPLETED, {"phase": "context", "duration_ms": 125}))
        state.apply(AgentEvent(EventKind.PHASE_COMPLETED, {"phase": "action", "duration_ms": 750}))
        state.apply(AgentEvent(EventKind.PHASE_COMPLETED, {"phase": "context", "duration_ms": 75}))
        state.apply(AgentEvent(EventKind.PHASE_COMPLETED, {"phase": "unknown", "duration_ms": 999}))
        state.apply(AgentEvent(EventKind.PHASE_COMPLETED, {"phase": "model", "duration_ms": -1}))

        self.assertEqual(state.phase_durations, {"context": 200, "action": 750})
        state.apply(AgentEvent(EventKind.RUN_STARTED, {}))
        self.assertEqual(state.phase_durations, {})

    def test_finish_run_records_duration_and_begin_run_clears_previous_completion(self) -> None:
        state = TerminalState()
        state.begin_run()
        state.finish_run(10.0, 12.345)
        self.assertEqual(state.completed_at, 12.345)
        self.assertEqual(state.completed_duration_ms, 2345)

        state.begin_run()
        self.assertIsNone(state.completed_at)
        self.assertIsNone(state.completed_duration_ms)

    def test_action_request_takes_precedence_over_waiting_for_model(self) -> None:
        state = TerminalState()
        state.apply(AgentEvent(EventKind.MODEL_STARTED, {}))
        state.apply(_text_delta("discard me"))

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
        self.assertEqual(state.active_action, "Read file src/a.py")
        self.assertFalse(state.has_draft)
        self.assertFalse(any(entry.kind is DisplayKind.PARTIAL_AGENT for entry in state.entries))

    def test_user_pause_cancellation_is_not_presented_as_an_error(self) -> None:
        state = TerminalState()
        state.begin_run()
        state.apply(AgentEvent(EventKind.CANCELLED, {"reason": "user requested pause"}))
        self.assertEqual(state.status, "paused")

    def test_paused_task_keeps_the_stop_reason_for_the_status_line(self) -> None:
        state = TerminalState()

        state.apply(AgentEvent(EventKind.TASK_PAUSED, {
            "task_id": "task-1",
            "status": "paused",
            "reason": "active time budget exceeded",
        }))

        self.assertEqual(state.status, "paused")
        self.assertEqual(state.task_stop_reason, "active time budget exceeded")

        state.apply(AgentEvent(EventKind.TASK_STATUS_CHANGED, {
            "task_id": "task-1",
            "status": "paused",
        }))

        self.assertEqual(state.task_stop_reason, "active time budget exceeded")

    def test_lease_warning_projects_renewal_and_convergence(self) -> None:
        state = TerminalState()
        state.apply(AgentEvent(EventKind.TASK_BUDGET_WARNING, {
            "category": "lease",
            "phase": "renewed",
            "tier": "standard",
            "renewals": 1,
            "reason": "new code generation",
        }))
        self.assertEqual(
            state.task_budget_line,
            "Lease renewed to standard (1): new code generation",
        )

        state.apply(AgentEvent(EventKind.TASK_BUDGET_WARNING, {
            "category": "lease",
            "phase": "converge",
            "tier": "standard",
            "renewals": 1,
            "reason": "soft lease exhausted without new trusted progress",
        }))
        self.assertEqual(
            state.task_budget_line,
            "Lease converging at standard: soft lease exhausted without new trusted progress",
        )

    def test_malformed_lease_warning_does_not_replace_status(self) -> None:
        state = TerminalState()
        state.task_budget_line = "existing"

        state.apply(AgentEvent(EventKind.TASK_BUDGET_WARNING, {
            "category": "lease",
            "phase": "renewed",
            "tier": "unknown",
            "renewals": "one",
            "reason": "invalid",
        }))

        self.assertEqual(state.task_budget_line, "existing")

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
        self.assertEqual(state.execution_summary, "1 action finished")

    def test_restore_projects_persisted_thread_state(self) -> None:
        state = TerminalState()
        state.restore(_restored_thread())

        self.assertEqual(state.thread_id, "thread-1")
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.summary[0], "goal: inspect repository")
        self.assertEqual(state.transcript, ["user: inspect", "assistant: done"])
        self.assertEqual(state.timeline[-2:], ["requested read_file", "completed read_file"])

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

    def test_adjacent_duplicate_assistant_messages_are_not_displayed_twice(self) -> None:
        state = TerminalState()
        message = Message(role="assistant", content="same answer")
        event = AgentEvent(EventKind.MESSAGE_ADDED, {"message": message.to_dict()})

        state.apply(event)
        state.apply(event)

        self.assertEqual(state.transcript, ["assistant: same answer"])
        self.assertEqual(
            len([entry for entry in state.entries if entry.kind is DisplayKind.AGENT]),
            1,
        )

    def test_restored_three_question_history_does_not_replay_last_answer(self) -> None:
        question_one = Message(role="user", content="nihao")
        answer_one = Message(role="assistant", content="你好")
        question_two = Message(role="user", content="这个项目历史有多少次提交？")
        answer_two = Message(role="assistant", content="当前项目历史共有 286 次提交。")
        question_three = Message(
            role="user", content="项目是从哪天开始的，那天的提交最多"
        )
        answer_three = Message(role="assistant", content="项目从 2026-07-10 开始。")
        state = TerminalState()
        state.restore(
            RestoredThread(
                thread_id="thread-1",
                messages=(
                    question_one,
                    answer_one,
                    question_two,
                    answer_two,
                    question_three,
                    answer_three,
                ),
                events=(),
                goals=(),
                checkpoints=(),
            )
        )

        # A restore/replay path may deliver the last durable MESSAGE_ADDED
        # once more. It must not append the already-restored answer again.
        state.apply(
            AgentEvent(
                EventKind.MESSAGE_ADDED,
                {"message": answer_three.to_dict()},
            )
        )

        self.assertEqual(
            state.transcript,
            [
                "user: nihao",
                "assistant: 你好",
                "user: 这个项目历史有多少次提交？",
                "assistant: 当前项目历史共有 286 次提交。",
                "user: 项目是从哪天开始的，那天的提交最多",
                "assistant: 项目从 2026-07-10 开始。",
            ],
        )

    def test_non_adjacent_duplicate_assistant_messages_are_preserved(self) -> None:
        state = TerminalState()
        for content in ("same answer", "other answer", "same answer"):
            state.apply(AgentEvent(
                EventKind.MESSAGE_ADDED,
                {"message": Message(role="assistant", content=content).to_dict()},
            ))

        self.assertEqual(
            state.transcript,
            ["assistant: same answer", "assistant: other answer", "assistant: same answer"],
        )

    def test_assistant_messages_with_tool_calls_are_not_deduplicated(self) -> None:
        state = TerminalState()
        message = Message(
            role="assistant",
            content="checking",
            tool_calls=(ToolCall("call-1", "read_file", {"path": "a.txt"}),),
        )
        event = AgentEvent(EventKind.MESSAGE_ADDED, {"message": message.to_dict()})

        state.apply(event)
        state.apply(event)

        self.assertEqual(
            state.transcript,
            ["assistant: checking", "assistant: checking"],
        )

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

    def test_empty_summary_error_is_rendered_as_a_recoverable_message(self) -> None:
        state = TerminalState()

        state.apply(AgentEvent(EventKind.ERROR, {"code": "empty_summary"}))

        self.assertEqual(state.entries[-1].kind, DisplayKind.ERROR)
        self.assertIn("模型未生成可显示的总结", state.entries[-1].text)

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
        self.assertEqual(state.entries[-1].text, "Read file")

    def test_action_summary_keeps_only_request_facts(self) -> None:
        state = TerminalState()
        state.apply(AgentEvent(EventKind.ACTION_REQUESTED, {"request": {"id": "call-1", "name": "read_file", "arguments": {"path": "src/a.py", "secret": "nope"}}}))
        state.apply(AgentEvent(EventKind.ACTION_COMPLETED, {"result": ActionResult("call-1", "read_file", {"content": "unbounded"}, metadata={"lines": 7, "duration_ms": 12}).to_dict()}))

        self.assertIn("Read file src/a.py", state.entries[-1].text)
        self.assertIn("lines: 7", state.entries[-1].text)
        self.assertNotIn("unbounded", state.entries[-1].text)
        self.assertNotIn("nope", state.entries[-1].text)

    def test_list_files_summary_has_target_count_duration_and_bounded_preview(self) -> None:
        state = TerminalState()
        state.apply(AgentEvent(EventKind.ACTION_REQUESTED, {"request": {
            "id": "call-1",
            "name": "list_files",
            "arguments": {"root": "."},
        }}))
        state.apply(AgentEvent(EventKind.ACTION_COMPLETED, {"result": ActionResult(
            "call-1",
            "list_files",
            {"files": [f"src/file-{index}.py" for index in range(8)]},
            metadata={"count": 500, "truncated": True, "duration_ms": 48_906},
        ).to_dict()}))

        summary = state.entries[-1].text
        self.assertIn("List files in .", summary)
        self.assertIn("500+ files", summary)
        self.assertIn("48.9s", summary)
        self.assertIn("src/file-0.py", summary)
        self.assertIn("… 3 more returned", summary)
        self.assertNotIn("src/file-5.py", summary)

    def test_reasoning_and_tool_call_streams_expose_lifecycle_not_private_text(self) -> None:
        state = TerminalState()
        state.apply(AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(
            ModelEventKind.REASONING_DELTA,
            text="private reasoning",
        ).to_dict()}))
        self.assertEqual(state.status, "reasoning")
        self.assertNotIn("private reasoning", state.draft_answer)

        state.apply(AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(
            ModelEventKind.TOOL_CALL,
            tool_call=ToolCall("call-1", "list_files", {"root": "."}),
        ).to_dict()}))
        self.assertEqual(state.status, "preparing_action")
        self.assertEqual(state.active_action, "List files in .")

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
