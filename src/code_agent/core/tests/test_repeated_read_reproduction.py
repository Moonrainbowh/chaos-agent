from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from code_agent.core._engine_turn import AgentEngineTurnMixin
from code_agent.core.engine_turn_feedback import circuit_breaker_result
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ActionResult, Message, ToolCall, ModelEvent, ModelEventKind, ToolDefinition
from code_agent.core.engine import AgentEngine
from code_agent.core.tests._engine_support import FakeActionDispatcher, FakeContextBuilder, FakeModelClient, MemorySessionRepository
from code_agent.interfaces.action_summary import action_summary
from code_agent.interfaces.terminal_state import TerminalState


class _FakeJournal:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []
        self.messages: list[Message] = []

    async def append_event(self, thread_id: str, event: AgentEvent) -> None:
        self.events.append(event)

    async def append_message(self, thread_id: str, message: Message) -> None:
        self.messages.append(message)

    def message_added(self, message: Message) -> AgentEvent:
        return AgentEvent(EventKind.MESSAGE_ADDED, {"message": message.to_dict()})


class _FailureHost:
    def __init__(self, journal: _FakeJournal) -> None:
        self._journal = journal

    async def _persist_tool_failure(self, thread_id, call, result):
        return await AgentEngineTurnMixin._persist_tool_failure(self, thread_id, call, result)

    def _track_turn_event(self, state, event, validation_blocked):
        if event.kind is EventKind.MESSAGE_ADDED:
            state.messages += (Message.from_dict(event.payload["message"]),)


class RepeatedReadReproductionTests(unittest.TestCase):
    def test_scripted_fake_model_three_identical_reads_breaks_on_third(self) -> None:
        history: list[str] = []
        call = ToolCall("id-1", "read_file", {"path": "startup.ps1"})
        results = [circuit_breaker_result(history, call) for _ in range(3)]
        self.assertEqual(results, [None, None, None])
        other = ToolCall("id-2", "write_file", {"path": "startup.ps1"})
        other_history: list[str] = []
        other_results = [circuit_breaker_result(other_history, other) for _ in range(3)]
        self.assertEqual([result is None for result in other_results], [True, True, False])

    def test_success_then_file_change_then_legal_reread_is_still_counted(self) -> None:
        history: list[str] = []
        call = ToolCall("id-1", "read_file", {"path": "startup.ps1"})
        success = ActionResult("id-1", "read_file", {"content": "old"}, False)
        changed = ActionResult("id-2", "write_file", {"changed": True}, False)
        self.assertFalse(success.is_error)
        self.assertFalse(changed.is_error)
        self.assertIsNone(circuit_breaker_result(history, call))
        self.assertIsNone(circuit_breaker_result(history, call))
        # No file state or result is consulted by the breaker; the third read is blocked.
        self.assertIsNone(circuit_breaker_result(history, call))
        self.assertEqual(len(history), 0)

    def test_circuit_breaker_feedback_has_request_and_bounded_ui_summary(self) -> None:
        journal = _FakeJournal()
        host = _FailureHost(journal)
        call = ToolCall("id-1", "write_file", {"path": "startup\x1b[2J.ps1", "encoding": "utf-8"})
        signature = f"write_file:{json.dumps(dict(call.arguments), sort_keys=True)}"
        result = circuit_breaker_result([signature] * 2, call)
        assert result is not None
        async def dispatch_blocked():
            state = SimpleNamespace(thread_id="thread-1", used_call_ids=set(), messages=(), action_history=[signature] * 2)
            turn = SimpleNamespace(tool_names={"write_file"})
            return [event async for event in AgentEngineTurnMixin._dispatch_turn_call(host, state, turn, call, [False])]
        events = asyncio.run(dispatch_blocked())
        self.assertEqual([event.kind for event in events], [EventKind.ACTION_REQUESTED, EventKind.ACTION_COMPLETED, EventKind.MESSAGE_ADDED])
        self.assertNotIn(EventKind.ACTION_STARTED, [event.kind for event in events])
        self.assertEqual(len(journal.messages), 1)
        self.assertEqual(json.loads(journal.messages[0].content)["output"]["error_code"], "repeated_action_blocked")
        state = TerminalState()
        for event in events:
            state.apply(event)
        rendered = state.entries[-1].text
        self.assertIn("startup.ps1", rendered)
        self.assertIn("blocked by policy", rendered)
        self.assertNotIn("\x1b", rendered)

    def test_blocked_call_id_is_registered_and_rejected_on_reuse(self) -> None:
        journal = _FakeJournal()
        host = _FailureHost(journal)
        call = ToolCall("same-id", "write_file", {"path": "startup.ps1"})
        async def dispatch_blocked():
            state = SimpleNamespace(thread_id="thread-1", used_call_ids=set(), messages=(), action_history=["write_file:{\"path\": \"startup.ps1\"}"] * 2)
            turn = SimpleNamespace(tool_names={"write_file"})
            events = [event async for event in AgentEngineTurnMixin._dispatch_turn_call(host, state, turn, call, [False])]
            return state, events
        state, _ = asyncio.run(dispatch_blocked())
        self.assertIn("same-id", state.used_call_ids)

    def test_fake_model_receives_single_paired_block_feedback_next_turn(self) -> None:
        calls = [ToolCall(f"call-{n}", "write_file", {"path": "startup.ps1"}) for n in range(1, 4)]
        streams = tuple((ModelEvent(ModelEventKind.TOOL_CALL, tool_call=call), ModelEvent(ModelEventKind.COMPLETED)) for call in calls)
        model = FakeModelClient(streams + ((ModelEvent(ModelEventKind.COMPLETED),),))
        result = ActionResult("result", "read_file", {"content": "ok"})
        sessions = MemorySessionRepository()
        actions = FakeActionDispatcher((ActionResult("result", "write_file", {"changed": True}), ActionResult("result", "write_file", {"changed": True})))
        actions._tools = (ToolDefinition("write_file", "Write", {"type": "object"}),)
        engine = AgentEngine(model, FakeContextBuilder(), actions, sessions)
        events = asyncio.run(self._collect(engine.run("inspect")))
        self.assertEqual(len(actions.requests), 2)
        self.assertEqual([event.kind for event in events].count(EventKind.ACTION_REQUESTED), 3)
        self.assertEqual([event.kind for event in events].count(EventKind.ACTION_STARTED), 2)
        self.assertEqual([event.kind for event in events].count(EventKind.ACTION_COMPLETED), 3)
        self.assertEqual([event.kind for event in events].count(EventKind.MESSAGE_ADDED), 8)
        feedback = model.calls[3][1]
        blocked = [message for message in feedback if message.role == "tool" and message.tool_call_id == "call-3"]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(json.loads(blocked[0].content)["output"]["error_code"], "repeated_action_blocked")

    @staticmethod
    async def _collect(iterator):
        return [event async for event in iterator]
