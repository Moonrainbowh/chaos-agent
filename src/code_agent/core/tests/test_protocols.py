from __future__ import annotations

import sys
import unittest
from collections.abc import AsyncIterator, Sequence
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core._session_io import SessionJournal  # noqa: E402
from code_agent.core.errors import SessionPersistenceError  # noqa: E402
from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.models import (  # noqa: E402
    ActionRequest,
    ActionResult,
    ContextBundle,
    Message,
    ModelEvent,
    ModelEventKind,
    ToolDefinition,
)
from code_agent.core.protocols import (  # noqa: E402
    ActionDispatcher,
    ContextBuilder,
    ModelClient,
    SessionRepository,
)
from code_agent.core.task_state import TaskState  # noqa: E402


class FakeModelClient:
    async def stream(
        self,
        system_prompt: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[ModelEvent]:
        yield ModelEvent(kind=ModelEventKind.COMPLETED)


class FakeContextBuilder:
    async def build(
        self,
        messages: Sequence[Message],
        user_input: str,
        tools: Sequence[ToolDefinition],
        task_state: TaskState,
    ) -> ContextBundle:
        return ContextBundle(system_prompt=user_input, messages=messages)


class FakeActionDispatcher:
    def tools(self) -> Sequence[ToolDefinition]:
        return ()

    async def dispatch(
        self, request: ActionRequest, cancellation: CancellationToken
    ) -> ActionResult:
        return ActionResult(
            request_id=request.id,
            name=request.name,
            output=None,
        )


class FakeSessionRepository:
    def __init__(self) -> None:
        self.task_state = TaskState.empty()

    async def create_thread(self) -> str:
        return "thread-1"

    async def load_messages(self, thread_id: str) -> Sequence[Message]:
        return ()

    async def append_message(
        self, thread_id: str, message: Message
    ) -> None:
        return None

    async def append_event(
        self, thread_id: str, event: AgentEvent
    ) -> None:
        return None

    async def load_task_state(self, thread_id: str) -> TaskState:
        return self.task_state

    async def save_task_state(self, thread_id: str, state: TaskState) -> None:
        self.task_state = state

    async def reduce_task_state(
        self, thread_id: str, request: ActionRequest, result: ActionResult
    ) -> TaskState:
        return self.task_state


class InvalidModelClient:
    stream = 42


class ProtocolImplementationTests(unittest.IsolatedAsyncioTestCase):
    async def test_protocol_does_not_claim_runtime_signature_compatibility(
        self,
    ) -> None:
        with self.assertRaises(TypeError):
            isinstance(InvalidModelClient(), ModelClient)

    async def test_model_client_fake_streams_events(self) -> None:
        client: ModelClient = FakeModelClient()

        events = [
            event
            async for event in client.stream(
                system_prompt="system",
                messages=(),
                tools=(),
            )
        ]

        self.assertEqual(
            events,
            [ModelEvent(kind=ModelEventKind.COMPLETED)],
        )

    async def test_context_builder_fake_builds_bundle(self) -> None:
        builder: ContextBuilder = FakeContextBuilder()
        message = Message(role="user", content="hello")

        bundle = await builder.build((message,), "system", (), TaskState.empty())

        self.assertEqual(
            bundle,
            ContextBundle(system_prompt="system", messages=(message,)),
        )

    async def test_action_dispatcher_fake_dispatches_request(self) -> None:
        dispatcher: ActionDispatcher = FakeActionDispatcher()
        request = ActionRequest(id="action-1", name="read_file", arguments={})

        result = await dispatcher.dispatch(request, CancellationToken())

        self.assertEqual(dispatcher.tools(), ())
        self.assertEqual(
            result,
            ActionResult(
                request_id="action-1",
                name="read_file",
                output=None,
            ),
        )

    async def test_session_repository_fake_supports_async_operations(
        self,
    ) -> None:
        repository: SessionRepository = FakeSessionRepository()
        message = Message(role="user", content="hello")
        event = AgentEvent(kind=EventKind.RUN_STARTED)

        thread_id = await repository.create_thread()

        self.assertEqual(thread_id, "thread-1")
        self.assertEqual(await repository.load_messages(thread_id), ())
        self.assertIsNone(await repository.append_message(thread_id, message))
        self.assertIsNone(await repository.append_event(thread_id, event))
        state = TaskState(objective="repair startup")
        self.assertIsNone(await repository.save_task_state(thread_id, state))
        self.assertEqual(await repository.load_task_state(thread_id), state)

    async def test_session_journal_saves_and_loads_task_state_through_contract(
        self,
    ) -> None:
        journal = SessionJournal(FakeSessionRepository())
        state = TaskState(objective="repair startup")

        await journal.save_task_state("thread-1", state)

        self.assertEqual(await journal.load_task_state("thread-1"), state)

    async def test_session_journal_normalizes_task_state_save_failure(self) -> None:
        class FailingRepository(FakeSessionRepository):
            async def save_task_state(self, thread_id: str, state: TaskState) -> None:
                raise RuntimeError("database detail")

        with self.assertRaisesRegex(SessionPersistenceError, "persist task state"):
            await SessionJournal(FailingRepository()).save_task_state(
                "thread-1", TaskState.empty()
            )


if __name__ == "__main__":
    unittest.main()
