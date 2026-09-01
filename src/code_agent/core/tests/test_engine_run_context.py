from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.engine import AgentEngine  # noqa: E402
from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.errors import ContextBuildError, SessionPersistenceError  # noqa: E402
from code_agent.core.events import EventKind  # noqa: E402
from code_agent.core.models import (  # noqa: E402
    ActionResult,
    Message,
    ModelEvent,
    ModelEventKind,
    ToolCall,
    ToolDefinition,
    Usage,
)
from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.task_state import TaskState  # noqa: E402
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.core.tests._engine_support import (  # noqa: E402
    FakeActionDispatcher,
    FakeContextBuilder,
    FakeModelClient,
    MemorySessionRepository,
)


def model_event(kind: ModelEventKind, **values: object) -> ModelEvent:
    return ModelEvent(kind=kind, **values)  # type: ignore[arg-type]


class AgentEngineRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_receives_active_thread_and_cancellation(self) -> None:
        class RecordingIdentityContext:
            def __init__(self) -> None:
                self.call: tuple[object, ...] | None = None

            async def build(
                self,
                thread_id: str,
                messages: object,
                user_input: str,
                tools: object,
                task_state: TaskState,
                cancellation: CancellationToken,
            ) -> object:
                self.call = (
                    thread_id,
                    messages,
                    user_input,
                    tools,
                    task_state,
                    cancellation,
                )
                return __import__(
                    "code_agent.core.models", fromlist=["ContextBundle"]
                ).ContextBundle(system_prompt="system", messages=messages)

        context = RecordingIdentityContext()
        cancellation = CancellationToken()
        engine = AgentEngine(
            FakeModelClient(((model_event(ModelEventKind.COMPLETED),),)),
            context,  # type: ignore[arg-type]
            FakeActionDispatcher(),
            MemorySessionRepository(),
        )

        events = [
            event
            async for event in engine.run("inspect", cancellation=cancellation)
        ]

        self.assertEqual(context.call[0], events[0].payload["thread_id"])
        self.assertIs(context.call[-1], cancellation)

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
        self.assertEqual(
            context.calls[0][:2],
            ((Message(role="user", content="Say hello"),), ""),
        )
        self.assertEqual(context.calls[0][3].__class__.__name__, "TaskState")
        self.assertEqual(model.calls[0][1], (Message(role="user", content="Say hello"),))
        self.assertEqual(events[-1].kind, EventKind.COMPLETED)
        self.assertEqual(events[-1].payload["usage"]["total_tokens"], 5)
        self.assertEqual(sessions.events[thread_id], events)

    async def test_peer_turn_wakes_engine_without_persisting_a_user_message(self) -> None:
        class PeerDispatcher(FakeActionDispatcher):
            def tools(self):
                return (
                    *super().tools(),
                    ToolDefinition(
                        name="send_message",
                        description="Send a peer message",
                        parameters={"type": "object"},
                    ),
                )

        sessions = MemorySessionRepository()
        thread_id = await sessions.create_thread()
        await sessions.append_message(
            thread_id, Message(role="user", content="original request")
        )
        context = FakeContextBuilder()
        model = FakeModelClient(
            ((
                model_event(ModelEventKind.TEXT_DELTA, text="peer noted"),
                model_event(ModelEventKind.COMPLETED),
            ),)
        )
        engine = AgentEngine(
            model,
            context,
            PeerDispatcher(),
            sessions,
            peer_tool_names=("send_message",),
        )

        events = [event async for event in engine.run_peer(thread_id=thread_id)]

        self.assertEqual(events[0].kind, EventKind.RUN_STARTED)
        self.assertEqual(
            sessions.messages[thread_id],
            [
                Message(role="user", content="original request"),
                Message(role="assistant", content="peer noted"),
            ],
        )
        self.assertEqual(
            context.calls[0][0],
            (Message(role="user", content="original request"),),
        )
        added = tuple(
            event for event in events if event.kind is EventKind.MESSAGE_ADDED
        )
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].payload["message"]["role"], "assistant")
        self.assertEqual(
            tuple(tool.name for tool in model.calls[0][2]), ("send_message",)
        )

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
            (
                (
                    Message(role="user", content="old"),
                    Message(role="user", content="new"),
                ),
                "",
            ),
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

if __name__ == "__main__":
    unittest.main()
