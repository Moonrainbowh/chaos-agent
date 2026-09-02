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

    async def test_persisted_steering_is_consumed_before_the_next_model_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sessions = SQLiteSessionRepository(Path(directory) / "sessions.sqlite3")
            thread_id = await sessions.create_thread()
            task = await sessions.create_task(
                thread_id,
                TaskContract("repair", TaskAuthorization.local_workspace(directory)),
            )
            task = await sessions.transition_task(task.id, TaskStatus.RUNNING)
            await sessions.append_message(thread_id, Message("user", "change direction"))
            await sessions.record_task_control(task.id, "change direction")
            context = FakeContextBuilder()
            engine = AgentEngine(
                FakeModelClient(((model_event(ModelEventKind.COMPLETED),),)),
                context,
                FakeActionDispatcher(),
                sessions,
            )

            _ = [event async for event in engine.run("repair", thread_id=thread_id, task=task)]

            self.assertIn(Message("user", "change direction"), context.calls[0][0])
            self.assertEqual(await sessions.consume_task_controls(task.id), ())

    async def test_queued_followup_is_promoted_only_after_current_turn_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sessions = SQLiteSessionRepository(Path(directory) / "sessions.sqlite3")
            thread_id = await sessions.create_thread()
            task = await sessions.create_task(
                thread_id,
                TaskContract("repair", TaskAuthorization.local_workspace(directory)),
            )
            task = await sessions.transition_task(task.id, TaskStatus.RUNNING)
            queued = Message("user", "then update the docs")
            await sessions.record_task_followup(task.id, queued, queued.content)
            context = FakeContextBuilder()
            engine = AgentEngine(
                FakeModelClient(
                    (
                        (model_event(ModelEventKind.COMPLETED),),
                        (model_event(ModelEventKind.COMPLETED),),
                    )
                ),
                context,
                FakeActionDispatcher(),
                sessions,
            )

            events = [
                event
                async for event in engine.run(
                    "repair", thread_id=thread_id, task=task
                )
            ]

            self.assertNotIn(queued, context.calls[0][0])
            self.assertIn(queued, context.calls[1][0])
            self.assertEqual(
                [event.kind for event in events].count(
                    EventKind.TASK_FOLLOWUPS_PROMOTED
                ),
                1,
            )

    async def test_followup_queued_during_verification_is_promoted_after_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sessions = SQLiteSessionRepository(Path(directory) / "sessions.sqlite3")
            thread_id = await sessions.create_thread()
            task = await sessions.create_task(
                thread_id,
                TaskContract("repair", TaskAuthorization.local_workspace(directory)),
            )
            task = await sessions.transition_task(task.id, TaskStatus.RUNNING)
            queued = Message("user", "use the new direction")

            class QueueDuringVerificationEngine(AgentEngine):
                verification_started = False

                async def _run_suggested_verification(self, *args: object):
                    if self.verification_started:
                        return None
                    self.verification_started = True
                    await sessions.record_task_followup(
                        task.id, queued, queued.content
                    )
                    return args[4], ()

            context = FakeContextBuilder()
            engine = QueueDuringVerificationEngine(
                FakeModelClient(
                    (
                        (model_event(ModelEventKind.COMPLETED),),
                        (model_event(ModelEventKind.COMPLETED),),
                    )
                ),
                context,
                FakeActionDispatcher(),
                sessions,
            )

            events = [
                event
                async for event in engine.run(
                    "repair", thread_id=thread_id, task=task
                )
            ]

            self.assertIn(queued, context.calls[1][0])
            self.assertEqual(
                [event.kind for event in events].count(
                    EventKind.TASK_FOLLOWUPS_PROMOTED
                ),
                1,
            )

    async def test_followup_queued_during_tool_is_in_the_next_model_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sessions = SQLiteSessionRepository(Path(directory) / "sessions.sqlite3")
            thread_id = await sessions.create_thread()
            task = await sessions.create_task(
                thread_id,
                TaskContract("repair", TaskAuthorization.local_workspace(directory)),
            )
            task = await sessions.transition_task(task.id, TaskStatus.RUNNING)
            queued = Message("user", "change the next step")

            class QueueDuringToolDispatcher(FakeActionDispatcher):
                async def dispatch(self, *args: object, **kwargs: object) -> ActionResult:
                    result = await super().dispatch(*args, **kwargs)  # type: ignore[arg-type]
                    await sessions.record_task_followup(
                        task.id, queued, queued.content
                    )
                    return result

            call = ToolCall("call-1", "read_file", {"path": "README.md"})
            model = FakeModelClient(
                (
                    (
                        model_event(ModelEventKind.TOOL_CALL, tool_call=call),
                        model_event(ModelEventKind.COMPLETED),
                    ),
                    (model_event(ModelEventKind.COMPLETED),),
                )
            )
            dispatcher = QueueDuringToolDispatcher(
                (ActionResult("call-1", "read_file", {"text": "ok"}),)
            )
            context = FakeContextBuilder()
            engine = AgentEngine(model, context, dispatcher, sessions)

            events = [
                event
                async for event in engine.run(
                    "repair", thread_id=thread_id, task=task
                )
            ]

            self.assertNotIn(queued, context.calls[0][0])
            self.assertIn(queued, context.calls[1][0])
            self.assertEqual(
                [event.kind for event in events].count(
                    EventKind.TASK_FOLLOWUPS_PROMOTED
                ),
                1,
            )

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
