from __future__ import annotations

import unittest

from code_agent.core.completion_contract import TaskIntent
from code_agent.core.engine import AgentEngine, EngineLimits
from code_agent.core.events import EventKind
from code_agent.core.models import ActionResult, ModelEvent, ModelEventKind, ToolCall, Usage
from code_agent.core.task import TaskAuthorization, TaskContract, TaskRecord, TaskStatus
from code_agent.core.tests._engine_support import (
    FakeActionDispatcher,
    FakeContextBuilder,
    FakeModelClient,
    MemorySessionRepository,
)


class _TaskSession(MemorySessionRepository):
    warned = False

    async def create_thread(self):
        self.thread_id = await super().create_thread()
        return self.thread_id

    async def record_task_active_seconds(self, task_id, active_seconds):
        return self.task_budgets[self.thread_id]

    async def consume_task_controls(self, task_id):
        return ()

    async def consume_task_usage(self, task_id, usage):
        return None

    async def mark_task_budget_warnings(self, task_id):
        if self.warned:
            return ()
        self.warned = True
        return (80,)

    async def promote_task_followups(self, task_id):
        return ()

    async def transition_task(self, task_id, status, reason=None):
        return self.task.transition(status, reason)

    async def create_checkpoint(self, thread_id, label, facts):
        return None


class EngineStagnationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_only_investigation_gets_evidence_replan_without_losing_tools(self):
        streams = []
        for index in range(5):
            call = ToolCall("read-" + str(index), "read_file", {"path": f"file-{index}.txt"})
            events = [ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=call)]
            if index == 0:
                events.append(ModelEvent(kind=ModelEventKind.USAGE, usage=Usage(8, 1)))
            events.append(ModelEvent(kind=ModelEventKind.COMPLETED))
            streams.append(tuple(events))
        call = ToolCall("read-5", "read_file", {"path": "file-5.txt"})
        streams.append((
            ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="Need one final comparison."),
            ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=call),
            ModelEvent(kind=ModelEventKind.COMPLETED),
        ))
        streams.append((
            ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="summary"),
            ModelEvent(kind=ModelEventKind.COMPLETED),
        ))
        sessions = _TaskSession()
        thread_id = await sessions.create_thread()
        task = TaskRecord(
            "analysis-task",
            thread_id,
            TaskContract(
                "inspect", TaskAuthorization.local_workspace("."), intent=TaskIntent.ANALYZE
            ),
            status=TaskStatus.RUNNING,
        )
        sessions.task = task
        actions = FakeActionDispatcher([
            ActionResult(f"read-{index}", "read_file", {"content": str(index)})
            for index in range(6)
        ])
        model = FakeModelClient(tuple(streams))
        engine = AgentEngine(
            model,
            FakeContextBuilder(),
            actions,
            sessions,
            limits=EngineLimits(max_agent_rounds=7),
        )

        events = [event async for event in engine.run("inspect", thread_id=thread_id, task=task)]

        self.assertEqual(len(actions.requests), 6)
        self.assertEqual(len(model.calls), 7)
        self.assertTrue(model.calls[5][2])
        self.assertEqual(model.calls[-1][2], ())
        self.assertIn(EventKind.COMPLETED, [event.kind for event in events])
        self.assertTrue(any(
            event.kind is EventKind.TASK_BUDGET_WARNING
            and event.payload.get("category") == "exploration"
            for event in events
        ))
        messages = sessions.messages[thread_id]
        self.assertTrue(any(message.role == "developer" for message in messages))
        roles = [message.role for message in messages]
        self.assertLess(roles.index("assistant"), roles.index("developer"))
        self.assertTrue(any(
            message.role == "developer" and "token budget reached 80%" in message.content
            for message in messages
        ))
        self.assertTrue(any(
            message.role == "developer" and "token budget reached 80%" in message.content
            for message in model.calls[1][1]
        ))
        self.assertTrue(any(
            message.role == "developer" and "avoid broad repeated exploration" in message.content
            for message in model.calls[3][1]
        ))
        self.assertTrue(any(
            message.role == "developer" and "state the evidence and remaining gap" in message.content
            for message in model.calls[5][1]
        ))

    async def test_modify_task_tool_only_loop_gets_a_no_tool_replan_turn(self):
        streams = []
        for index in range(5):
            call = ToolCall("read-" + str(index), "read_file", {"path": f"file-{index}.txt"})
            streams.append((
                ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=call),
                ModelEvent(kind=ModelEventKind.COMPLETED),
            ))
        streams.append((
            ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="replan"),
            ModelEvent(kind=ModelEventKind.COMPLETED),
        ))
        sessions = _TaskSession()
        thread_id = await sessions.create_thread()
        task = TaskRecord(
            "modify-task", thread_id,
            TaskContract("fix", TaskAuthorization.local_workspace("."), intent=TaskIntent.MODIFY),
            status=TaskStatus.RUNNING,
        )
        sessions.task = task
        actions = FakeActionDispatcher([
            ActionResult(f"read-{index}", "read_file", {"content": str(index)})
            for index in range(5)
        ])
        model = FakeModelClient(tuple(streams))
        engine = AgentEngine(
            model, FakeContextBuilder(), actions, sessions,
            limits=EngineLimits(max_agent_rounds=6),
        )

        events = [event async for event in engine.run("fix", thread_id=thread_id, task=task)]

        self.assertEqual(len(actions.requests), 5)
        self.assertEqual(model.calls[-1][2], ())
        self.assertTrue(any(
            event.kind is EventKind.TASK_BUDGET_WARNING
            and event.payload.get("phase") == "replan"
            for event in events
        ))

    async def test_engine_emits_context_model_and_action_timing_events(self):
        call = ToolCall("read-1", "read_file", {"path": "file.txt"})
        model = FakeModelClient((
            (
                ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=call),
                ModelEvent(kind=ModelEventKind.COMPLETED),
            ),
            (
                ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="done"),
                ModelEvent(kind=ModelEventKind.COMPLETED),
            ),
        ))
        sessions = MemorySessionRepository()
        engine = AgentEngine(
            model, FakeContextBuilder(),
            FakeActionDispatcher([ActionResult("read-1", "read_file", {"content": "ok"})]),
            sessions,
            limits=EngineLimits(max_agent_rounds=2),
        )

        events = [event async for event in engine.run("inspect")]

        timings = [event.payload for event in events if event.kind is EventKind.PHASE_COMPLETED]
        self.assertEqual([item["phase"] for item in timings].count("context"), 2)
        self.assertEqual([item["phase"] for item in timings].count("model"), 2)
        self.assertEqual([item["phase"] for item in timings].count("action"), 1)
        self.assertTrue(all(isinstance(item["duration_ms"], int) and item["duration_ms"] >= 0 for item in timings))

    async def test_final_no_tool_answer_does_not_persist_stale_budget_notice(self):
        sessions = _TaskSession()
        thread_id = await sessions.create_thread()
        task = TaskRecord(
            "analysis-task", thread_id,
            TaskContract("inspect", TaskAuthorization.local_workspace("."), intent=TaskIntent.ANALYZE),
            status=TaskStatus.RUNNING,
        )
        sessions.task = task
        model = FakeModelClient((
            (
                ModelEvent(kind=ModelEventKind.USAGE, usage=Usage(8, 1)),
                ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="answer"),
                ModelEvent(kind=ModelEventKind.COMPLETED),
            ),
        ))
        engine = AgentEngine(
            model, FakeContextBuilder(), FakeActionDispatcher(), sessions,
            limits=EngineLimits(max_agent_rounds=1),
        )

        events = [event async for event in engine.run("inspect", thread_id=thread_id, task=task)]

        self.assertIn(EventKind.COMPLETED, [event.kind for event in events])
        self.assertFalse(any(
            message.role == "developer" and "token budget reached 80%" in message.content
            for message in sessions.messages[thread_id]
        ))
        self.assertTrue(any(
            message.role == "developer" and "final available model turn" in message.content
            for message in sessions.messages[thread_id]
        ))

    async def test_final_turn_tool_call_is_rejected_without_dispatch(self):
        streams = []
        for index in range(5):
            call = ToolCall("read-" + str(index), "read_file", {"path": f"file-{index}.txt"})
            streams.append((
                ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=call),
                ModelEvent(kind=ModelEventKind.COMPLETED),
            ))
        streams.append((
            ModelEvent(
                kind=ModelEventKind.TOOL_CALL,
                tool_call=ToolCall("late", "read_file", {"path": "late.txt"}),
            ),
            ModelEvent(kind=ModelEventKind.COMPLETED),
        ))
        sessions = _TaskSession()
        thread_id = await sessions.create_thread()
        task = TaskRecord(
            "analysis-task", thread_id,
            TaskContract("inspect", TaskAuthorization.local_workspace("."), intent=TaskIntent.ANALYZE),
            status=TaskStatus.RUNNING,
        )
        sessions.task = task
        actions = FakeActionDispatcher([
            ActionResult(f"read-{index}", "read_file", {"content": str(index)})
            for index in range(5)
        ])
        model = FakeModelClient(tuple(streams))
        engine = AgentEngine(
            model, FakeContextBuilder(), actions, sessions,
            limits=EngineLimits(max_agent_rounds=6),
        )

        events = [event async for event in engine.run("inspect", thread_id=thread_id, task=task)]

        self.assertEqual(len(actions.requests), 5)
        self.assertEqual(len(model.calls), 6)
        self.assertIn(EventKind.TASK_PAUSED, [event.kind for event in events])
        self.assertTrue(any(
            event.kind is EventKind.ACTION_COMPLETED
            and event.payload.get("result", {}).get("output", {}).get("error_code")
            == "summary_tool_call_rejected"
            for event in events
        ))


if __name__ == "__main__":
    unittest.main()
