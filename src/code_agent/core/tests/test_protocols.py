from __future__ import annotations

import sys
import unittest
from collections.abc import AsyncIterator, Sequence
from inspect import Parameter, signature
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.action_execution import ActionExecutionContext  # noqa: E402
from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.context_request import ContextRequest  # noqa: E402
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
from code_agent.core.task import TaskAuthorization  # noqa: E402
from code_agent.core.task_state import TaskState  # noqa: E402

message = Message(role="user", content="hello")


def context_request(**updates: object) -> ContextRequest:
    values = {
        "thread_id": "thread-1",
        "revision": 1,
        "messages": (message,),
        "user_input": "system",
        "tools": (),
        "task_state": TaskState.empty(),
        "cancellation": CancellationToken(),
    }
    values.update(updates)
    return ContextRequest(**values)  # type: ignore[arg-type]


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
        thread_id: str,
        messages: Sequence[Message],
        user_input: str,
        tools: Sequence[ToolDefinition],
        task_state: TaskState,
        cancellation: CancellationToken,
    ) -> ContextBundle:
        return ContextBundle(system_prompt=user_input, messages=messages)


class FakeActionDispatcher:
    def __init__(self) -> None:
        self.context: ActionExecutionContext | None = None

    def tools(self) -> Sequence[ToolDefinition]:
        return ()

    async def dispatch(
        self,
        request: ActionRequest,
        cancellation: CancellationToken,
        task_authorization: TaskAuthorization | None = None,
        *,
        execution_context: ActionExecutionContext | None = None,
    ) -> ActionResult:
        self.context = execution_context
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
        request = context_request()

        bundle = await builder.build(
            "thread-1",
            (message,),
            "system",
            (),
            TaskState.empty(),
            CancellationToken(),
        )

        self.assertEqual(
            bundle,
            ContextBundle(system_prompt="system", messages=request.messages),
        )

    def test_context_request_rejects_invalid_identity_and_typed_values(self) -> None:
        invalid_values = (
            ("thread_id", " "),
            ("revision", 0),
            ("revision", True),
            ("user_input", object()),
            ("messages", []),
            ("messages", (object(),)),
            ("tools", []),
            ("tools", (object(),)),
            ("task_state", object()),
            ("cancellation", object()),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=value), self.assertRaises((TypeError, ValueError)):
                context_request(**{field: value})

    def test_context_request_rejects_invalid_pressure_and_timeout(self) -> None:
        invalid_values = (
            ("context_pressure", True),
            ("context_pressure", float("inf")),
            ("context_pressure", -0.1),
            ("context_pressure", 1.1),
            ("timeout_seconds", True),
            ("timeout_seconds", float("nan")),
            ("timeout_seconds", 0),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=value), self.assertRaises((TypeError, ValueError)):
                context_request(**{field: value})

        self.assertEqual(context_request(timeout_seconds=10**1_000).timeout_seconds, 10**1_000)

    def test_context_request_freezes_and_validates_json_mappings(self) -> None:
        for field in ("mode_snapshot", "permission_snapshot"):
            source = {"nested": [1, {"enabled": True}]}
            request = context_request(**{field: source})
            source["nested"].append(2)
            frozen = getattr(request, field)
            self.assertEqual(frozen["nested"], (1, {"enabled": True}))
            with self.assertRaises(TypeError):
                frozen["extra"] = 1
            for invalid in ([], {"bad": object()}, {"bad": float("inf")}):
                with self.subTest(field=field, invalid=invalid), self.assertRaises((TypeError, ValueError)):
                    context_request(**{field: invalid})

        source = {"model_turns": 1}
        request = context_request(budget_lease=source)
        source["model_turns"] = 2
        self.assertEqual(request.budget_lease["model_turns"], 1)
        with self.assertRaises(TypeError):
            request.budget_lease["model_turns"] = 2

    def test_context_request_rejects_non_integer_budget_values(self) -> None:
        with self.assertRaises(TypeError):
            context_request(budget_lease=[])  # type: ignore[arg-type]
        for value in ("1", [1], {"value": 1}, 1.0, True, -1):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                context_request(budget_lease={"model_turns": value})

    def test_action_dispatcher_declares_keyword_only_context(self) -> None:
        parameter = signature(ActionDispatcher.dispatch).parameters[
            "execution_context"
        ]

        self.assertIs(parameter.kind, Parameter.KEYWORD_ONLY)
        self.assertIsNone(parameter.default)

    async def test_action_dispatcher_fake_dispatches_request(self) -> None:
        dispatcher: ActionDispatcher = FakeActionDispatcher()
        request = ActionRequest(id="action-1", name="read_file", arguments={})
        context = ActionExecutionContext("owner", "origin", "action-1")

        result = await dispatcher.dispatch(
            request, CancellationToken(), execution_context=context
        )

        self.assertEqual(dispatcher.tools(), ())
        self.assertEqual(dispatcher.context, context)
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
