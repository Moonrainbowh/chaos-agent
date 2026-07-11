from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.engine import AgentEngine, EngineLimits  # noqa: E402
from code_agent.core.errors import EngineLimitError, ModelStreamError  # noqa: E402
from code_agent.core.events import EventKind  # noqa: E402
from code_agent.core.models import (  # noqa: E402
    ModelEvent,
    ModelEventKind,
    ToolCall,
    Usage,
)
from code_agent.core.tests._engine_support import (  # noqa: E402
    FakeActionDispatcher,
    FakeContextBuilder,
    FakeModelClient,
    MemorySessionRepository,
)


class AgentEngineLimitTests(unittest.IsolatedAsyncioTestCase):
    def test_limits_reject_bool_and_out_of_range_values(self) -> None:
        for values in (
            {"max_model_turns": 0},
            {"max_tool_calls": -1},
            {"max_total_tokens": 0},
            {"max_assistant_chars": 0},
            {"max_tool_calls": True},
        ):
            with self.subTest(values=values):
                with self.assertRaises((TypeError, ValueError)):
                    EngineLimits(**values)  # type: ignore[arg-type]

    def make_engine(
        self,
        model: FakeModelClient,
        sessions: MemorySessionRepository,
        *,
        limits: EngineLimits | None = None,
        actions: FakeActionDispatcher | None = None,
    ) -> AgentEngine:
        return AgentEngine(
            model,
            FakeContextBuilder(),
            actions or FakeActionDispatcher(),
            sessions,
            limits=limits,
        )

    async def test_tool_budget_fails_before_partial_dispatch(self) -> None:
        calls = (
            ToolCall(id="call-1", name="read_file", arguments={}),
            ToolCall(id="call-2", name="read_file", arguments={}),
        )
        model = FakeModelClient(
            ((
                *(ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=call) for call in calls),
                ModelEvent(kind=ModelEventKind.COMPLETED),
            ),)
        )
        actions = FakeActionDispatcher()
        sessions = MemorySessionRepository()
        engine = self.make_engine(
            model,
            sessions,
            limits=EngineLimits(max_tool_calls=1),
            actions=actions,
        )

        with self.assertRaises(EngineLimitError):
            _ = [event async for event in engine.run("inspect")]

        self.assertEqual(actions.requests, [])
        self.assertEqual(sessions.events["thread-1"][-1].kind, EventKind.ERROR)

    async def test_token_budget_stops_stream_and_persists_error(self) -> None:
        model = FakeModelClient(
            ((
                ModelEvent(kind=ModelEventKind.USAGE, usage=Usage(8, 3)),
                ModelEvent(kind=ModelEventKind.COMPLETED),
            ),)
        )
        sessions = MemorySessionRepository()
        engine = self.make_engine(
            model,
            sessions,
            limits=EngineLimits(max_total_tokens=10),
        )

        with self.assertRaises(EngineLimitError):
            _ = [event async for event in engine.run("inspect")]

        self.assertEqual(sessions.events["thread-1"][-1].kind, EventKind.ERROR)
        self.assertNotIn(EventKind.COMPLETED, [e.kind for e in sessions.events["thread-1"]])

    async def test_output_budget_rejects_oversized_delta_without_persisting_it(self) -> None:
        model = FakeModelClient(
            ((
                ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="12345"),
                ModelEvent(kind=ModelEventKind.COMPLETED),
            ),)
        )
        sessions = MemorySessionRepository()
        engine = self.make_engine(
            model,
            sessions,
            limits=EngineLimits(max_assistant_chars=4),
        )

        with self.assertRaises(EngineLimitError):
            _ = [event async for event in engine.run("inspect")]

        model_events = [
            event for event in sessions.events["thread-1"]
            if event.kind is EventKind.MODEL_EVENT
        ]
        self.assertEqual(model_events, [])

    async def test_duplicate_tool_ids_fail_before_any_dispatch(self) -> None:
        duplicate = ToolCall(id="call-1", name="read_file", arguments={})
        model = FakeModelClient(
            ((
                ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=duplicate),
                ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=duplicate),
                ModelEvent(kind=ModelEventKind.COMPLETED),
            ),)
        )
        actions = FakeActionDispatcher()
        sessions = MemorySessionRepository()
        engine = self.make_engine(model, sessions, actions=actions)

        with self.assertRaises(ModelStreamError):
            _ = [event async for event in engine.run("inspect")]

        self.assertEqual(actions.requests, [])

    async def test_last_allowed_turn_does_not_start_unreturnable_tool(self) -> None:
        call = ToolCall(id="call-1", name="read_file", arguments={})
        model = FakeModelClient(
            ((
                ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=call),
                ModelEvent(kind=ModelEventKind.COMPLETED),
            ),)
        )
        actions = FakeActionDispatcher()
        sessions = MemorySessionRepository()
        engine = self.make_engine(
            model,
            sessions,
            limits=EngineLimits(max_model_turns=1),
            actions=actions,
        )

        with self.assertRaises(EngineLimitError):
            _ = [event async for event in engine.run("inspect")]

        self.assertEqual(actions.requests, [])

    async def test_missing_completed_event_is_a_protocol_error(self) -> None:
        sessions = MemorySessionRepository()
        engine = self.make_engine(
            FakeModelClient(((ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="partial"),),)),
            sessions,
        )

        with self.assertRaises(ModelStreamError):
            _ = [event async for event in engine.run("inspect")]

        self.assertEqual(sessions.events["thread-1"][-1].kind, EventKind.ERROR)
        self.assertNotIn("partial", str(sessions.events["thread-1"][-1].payload))

    async def test_cancellation_emits_terminal_cancelled_event(self) -> None:
        token = CancellationToken()
        token.cancel("user stop")
        sessions = MemorySessionRepository()
        engine = self.make_engine(FakeModelClient(()), sessions)

        events = [event async for event in engine.run("inspect", cancellation=token)]

        self.assertEqual(events[-1].kind, EventKind.CANCELLED)
        self.assertEqual(events[-1].payload["reason"], "user stop")
        self.assertNotIn(EventKind.COMPLETED, [event.kind for event in events])


if __name__ == "__main__":
    unittest.main()
