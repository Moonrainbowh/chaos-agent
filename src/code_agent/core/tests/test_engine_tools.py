from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.engine import AgentEngine  # noqa: E402
from code_agent.core.errors import (  # noqa: E402
    ModelStreamError,
    SessionPersistenceError,
)
from code_agent.core.events import EventKind  # noqa: E402
from code_agent.core.models import (  # noqa: E402
    ActionRequest,
    ActionResult,
    Message,
    ModelEvent,
    ModelEventKind,
    ToolCall,
    ToolDefinition,
)
from code_agent.core.tests._engine_support import (  # noqa: E402
    FakeActionDispatcher,
    FakeContextBuilder,
    FakeModelClient,
    MemorySessionRepository,
)


def completed() -> ModelEvent:
    return ModelEvent(kind=ModelEventKind.COMPLETED)


class AgentEngineToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_result_is_paired_and_returned_to_next_model_turn(self) -> None:
        call = ToolCall(id="call-1", name="read_file", arguments={"path": "a.txt"})
        model = FakeModelClient(
            (
                (ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=call), completed()),
                (ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="done"), completed()),
            )
        )
        result = ActionResult(
            request_id="call-1",
            name="read_file",
            output={"text": "contents"},
        )
        actions = FakeActionDispatcher((result,))
        context = FakeContextBuilder()
        sessions = MemorySessionRepository()
        engine = AgentEngine(model, context, actions, sessions)

        events = [event async for event in engine.run("inspect")]

        self.assertEqual(
            actions.requests,
            [ActionRequest(id="call-1", name="read_file", arguments={"path": "a.txt"})],
        )
        messages = sessions.messages["thread-1"]
        self.assertEqual(messages[1], Message(role="assistant", tool_calls=(call,)))
        self.assertEqual(messages[2].role, "tool")
        self.assertEqual(messages[2].tool_call_id, "call-1")
        self.assertEqual(json.loads(messages[2].content), result.to_dict())
        self.assertEqual(context.calls[1][1], "")
        self.assertEqual(model.calls[1][1], tuple(messages[:3]))
        self.assertEqual(events[-1].payload["tool_calls"], 1)
        self.assertIn(EventKind.ACTION_COMPLETED, [event.kind for event in events])
        self.assertEqual(sessions.task_states["thread-1"].files_read, ("a.txt",))
        self.assertEqual(context.calls[1][3].files_read, ("a.txt",))

    async def test_dispatch_exception_becomes_sanitized_tool_error_feedback(self) -> None:
        call = ToolCall(id="call-1", name="read_file", arguments={})
        model = FakeModelClient(
            (
                (ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=call), completed()),
                (ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="recovered"), completed()),
            )
        )
        sessions = MemorySessionRepository()
        engine = AgentEngine(
            model,
            FakeContextBuilder(),
            FakeActionDispatcher((RuntimeError("secret-value"),)),
            sessions,
        )

        events = [event async for event in engine.run("inspect")]

        tool_message = sessions.messages["thread-1"][2]
        payload = json.loads(tool_message.content)
        self.assertTrue(payload["is_error"])
        self.assertEqual(payload["output"]["error"], "tool execution failed")
        self.assertNotIn("secret-value", tool_message.content)
        self.assertNotIn("secret-value", repr(events))
        self.assertEqual(events[-1].kind, EventKind.COMPLETED)

    async def test_unadvertised_tool_is_not_dispatched_and_gets_error_feedback(self) -> None:
        call = ToolCall(id="call-1", name="delete_everything", arguments={})
        model = FakeModelClient(
            (
                (ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=call), completed()),
                (ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="recovered"), completed()),
            )
        )
        actions = FakeActionDispatcher()
        sessions = MemorySessionRepository()
        engine = AgentEngine(model, FakeContextBuilder(), actions, sessions)

        events = [event async for event in engine.run("inspect")]

        self.assertEqual(actions.requests, [])
        feedback = json.loads(sessions.messages["thread-1"][2].content)
        self.assertTrue(feedback["is_error"])
        self.assertEqual(feedback["output"]["error"], "tool is not available")
        self.assertNotIn(EventKind.ACTION_STARTED, [event.kind for event in events])

    async def test_engine_passes_one_tool_tuple_to_context_then_model(self) -> None:
        model = FakeModelClient(((completed(),),))
        actions = FakeActionDispatcher()
        context = FakeContextBuilder()
        sessions = MemorySessionRepository()
        engine = AgentEngine(model, context, actions, sessions)

        events = [event async for event in engine.run("inspect")]

        self.assertEqual(events[-1].kind, EventKind.COMPLETED)
        self.assertIs(context.calls[0][2], model.calls[0][2])
        self.assertEqual(context.calls[0][2], tuple(actions.tools()))

    async def test_reducer_failure_prevents_completed_event_and_tool_feedback(self) -> None:
        class FailingReducerSessions(MemorySessionRepository):
            async def reduce_task_state(
                self,
                thread_id: str,
                request: ActionRequest,
                result: ActionResult,
            ) -> object:
                raise RuntimeError("database detail")

        call = ToolCall(id="call-1", name="read_file", arguments={"path": "a.txt"})
        model = FakeModelClient(
            ((ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=call), completed()),)
        )
        sessions = FailingReducerSessions()
        engine = AgentEngine(
            model,
            FakeContextBuilder(),
            FakeActionDispatcher(
                (ActionResult("call-1", "read_file", {"text": "contents"}),)
            ),
            sessions,
        )
        events = []

        with self.assertRaises(SessionPersistenceError):
            async for event in engine.run("inspect"):
                events.append(event)

        kinds = [event.kind for event in events]
        self.assertNotIn(EventKind.ACTION_COMPLETED, kinds)
        self.assertEqual(sessions.messages["thread-1"], [
            Message(role="user", content="inspect"),
            Message(role="assistant", tool_calls=(call,)),
        ])
        self.assertEqual(events[-1].kind, EventKind.ERROR)
        self.assertEqual(events[-1].payload["code"], "session_persistence")

    async def test_duplicate_advertised_tools_fail_before_context_and_model(self) -> None:
        model = FakeModelClient(())
        actions = FakeActionDispatcher()
        actions._tools = (
            ToolDefinition("read_file", "Read a file", {"type": "object"}),
            ToolDefinition("read_file", "Read another file", {"type": "object"}),
        )
        context = FakeContextBuilder()
        sessions = MemorySessionRepository()
        engine = AgentEngine(model, context, actions, sessions)

        with self.assertRaises(ModelStreamError):
            _ = [event async for event in engine.run("inspect")]

        self.assertEqual(context.calls, [])
        self.assertEqual(model.calls, [])
        self.assertEqual(sessions.events["thread-1"][-1].kind, EventKind.ERROR)

    async def test_tools_failure_becomes_terminal_error_after_user_persistence(self) -> None:
        class RaisingToolsDispatcher(FakeActionDispatcher):
            def tools(self) -> tuple[ToolDefinition, ...]:
                raise RuntimeError("untrusted dispatcher failure")

        model = FakeModelClient(())
        context = FakeContextBuilder()
        sessions = MemorySessionRepository()
        engine = AgentEngine(model, context, RaisingToolsDispatcher(), sessions)

        with self.assertRaises(ModelStreamError):
            _ = [event async for event in engine.run("inspect")]

        self.assertEqual(sessions.messages["thread-1"], [Message(role="user", content="inspect")])
        self.assertEqual(context.calls, [])
        self.assertEqual(model.calls, [])
        self.assertEqual(sessions.events["thread-1"][-1].kind, EventKind.ERROR)

    async def test_invalid_advertised_tool_becomes_terminal_error(self) -> None:
        model = FakeModelClient(())
        actions = FakeActionDispatcher()
        actions._tools = (object(),)  # type: ignore[assignment]
        context = FakeContextBuilder()
        sessions = MemorySessionRepository()
        engine = AgentEngine(model, context, actions, sessions)

        with self.assertRaises(ModelStreamError):
            _ = [event async for event in engine.run("inspect")]

        self.assertEqual(context.calls, [])
        self.assertEqual(model.calls, [])
        self.assertEqual(sessions.events["thread-1"][-1].kind, EventKind.ERROR)


if __name__ == "__main__":
    unittest.main()
