from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.engine import AgentEngine  # noqa: E402
from code_agent.core.errors import ContextBuildError, SessionPersistenceError  # noqa: E402
from code_agent.core.events import EventKind  # noqa: E402
from code_agent.core.models import (  # noqa: E402
    Message,
    ModelEvent,
    ModelEventKind,
    Usage,
)
from code_agent.core.task_state import TaskState  # noqa: E402
from code_agent.core.tests._engine_support import (  # noqa: E402
    FakeActionDispatcher,
    FakeContextBuilder,
    FakeModelClient,
    MemorySessionRepository,
)


def model_event(kind: ModelEventKind, **values: object) -> ModelEvent:
    return ModelEvent(kind=kind, **values)  # type: ignore[arg-type]


class AgentEngineRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_event_records_numeric_measurements_without_prompt_text(self) -> None:
        measurements = {
            "prompt_tokens": 20_000,
            "rule_tokens": 321,
            "tool_tokens": 123,
            "task_state_tokens": 45,
            "repo_map_tokens": 2_000,
            "message_tokens": 12_000,
            "removed_message_count": 2,
            "cache_hits": 7,
            "cache_misses": 3,
        }
        engine = AgentEngine(
            FakeModelClient(((model_event(ModelEventKind.COMPLETED),),)),
            FakeContextBuilder(measurements),
            FakeActionDispatcher(),
            MemorySessionRepository(),
        )

        events = [event async for event in engine.run("inspect secret.txt")]
        context_event = next(
            event for event in events if event.kind is EventKind.CONTEXT_BUILT
        )

        self.assertEqual(context_event.payload["prompt_tokens"], 20_000)
        self.assertEqual(
            {key: context_event.payload[key] for key in measurements},
            measurements,
        )
        self.assertTrue(
            all(isinstance(value, int) for value in context_event.payload.values())
        )
        self.assertNotIn("secret.txt", str(context_event.payload))

    async def test_final_answer_is_streamed_and_persisted(self) -> None:
        model = FakeModelClient(
            ((
                model_event(ModelEventKind.TEXT_DELTA, text="Hello "),
                model_event(ModelEventKind.TEXT_DELTA, text="world"),
                model_event(ModelEventKind.USAGE, usage=Usage(3, 2)),
                model_event(ModelEventKind.COMPLETED),
            ),)
        )
        context = FakeContextBuilder()
        sessions = MemorySessionRepository()
        engine = AgentEngine(
            model=model,
            context=context,
            actions=FakeActionDispatcher(),
            sessions=sessions,
        )

        events = [event async for event in engine.run("Say hello")]

        thread_id = events[0].payload["thread_id"]
        self.assertEqual(thread_id, "thread-1")
        self.assertEqual(
            sessions.messages[thread_id],
            [
                Message(role="user", content="Say hello"),
                Message(role="assistant", content="Hello world"),
            ],
        )
        self.assertEqual(context.calls[0][:2], ((), "Say hello"))
        self.assertEqual(context.calls[0][3].__class__.__name__, "TaskState")
        self.assertEqual(model.calls[0][1], (Message(role="user", content="Say hello"),))
        self.assertEqual(events[-1].kind, EventKind.COMPLETED)
        self.assertEqual(events[-1].payload["usage"]["total_tokens"], 5)
        self.assertEqual(sessions.events[thread_id], events)

    async def test_existing_thread_resumes_without_duplicate_user_message(self) -> None:
        model = FakeModelClient(
            ((
                model_event(ModelEventKind.TEXT_DELTA, text="next"),
                model_event(ModelEventKind.COMPLETED),
            ),)
        )
        context = FakeContextBuilder()
        sessions = MemorySessionRepository()
        thread_id = await sessions.create_thread()
        sessions.messages[thread_id].append(Message(role="user", content="old"))
        engine = AgentEngine(model, context, FakeActionDispatcher(), sessions)

        events = [
            event
            async for event in engine.run("new", thread_id=thread_id)
        ]

        self.assertEqual(sessions.created, 1)
        self.assertEqual(
            context.calls[0][:2],
            ((Message(role="user", content="old"),), "new"),
        )
        self.assertEqual(
            sessions.messages[thread_id],
            [
                Message(role="user", content="old"),
                Message(role="user", content="new"),
                Message(role="assistant", content="next"),
            ],
        )
        self.assertEqual(events[0].payload["thread_id"], thread_id)

    async def test_engine_loads_persisted_task_state_before_context_build(self) -> None:
        sessions = MemorySessionRepository()
        thread_id = await sessions.create_thread()
        sessions.task_states[thread_id] = TaskState(objective="repair startup")
        context = FakeContextBuilder()
        engine = AgentEngine(
            FakeModelClient(((model_event(ModelEventKind.COMPLETED),),)),
            context,
            FakeActionDispatcher(),
            sessions,
        )

        _ = [event async for event in engine.run("inspect", thread_id=thread_id)]

        self.assertEqual(context.calls[0][3].objective, "repair startup")

    async def test_blank_user_input_is_rejected_before_thread_creation(self) -> None:
        sessions = MemorySessionRepository()
        engine = AgentEngine(
            FakeModelClient(()),
            FakeContextBuilder(),
            FakeActionDispatcher(),
            sessions,
        )

        with self.assertRaises(ValueError):
            _ = [event async for event in engine.run("  ")]

        self.assertEqual(sessions.created, 0)

    async def test_invalid_context_result_is_reported_without_raw_exception(self) -> None:
        class InvalidContext:
            async def build(self, messages: object, user_input: str) -> object:
                return object()

        sessions = MemorySessionRepository()
        engine = AgentEngine(
            FakeModelClient(()),
            InvalidContext(),  # type: ignore[arg-type]
            FakeActionDispatcher(),
            sessions,
        )

        with self.assertRaises(ContextBuildError):
            _ = [event async for event in engine.run("inspect")]

        self.assertEqual(sessions.events["thread-1"][-1].kind, EventKind.ERROR)

    async def test_invalid_persisted_message_fails_closed(self) -> None:
        sessions = MemorySessionRepository()
        thread_id = await sessions.create_thread()
        sessions.messages[thread_id].append("invalid")  # type: ignore[arg-type]
        engine = AgentEngine(
            FakeModelClient(()),
            FakeContextBuilder(),
            FakeActionDispatcher(),
            sessions,
        )

        with self.assertRaises(SessionPersistenceError):
            _ = [
                event
                async for event in engine.run("inspect", thread_id=thread_id)
            ]


if __name__ == "__main__":
    unittest.main()
