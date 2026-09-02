from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import acp.schema as schema
from acp import PROTOCOL_VERSION, RequestError

from code_agent.acp.adapter import ChaosAcpAgent
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import (
    ActionRequest,
    ActionResult,
    Message,
    ModelEvent,
    ModelEventKind,
)
from code_agent.sessions.errors import SessionNotFound


class FakeClient:
    def __init__(self) -> None:
        self.updates: list[tuple[str, object]] = []

    async def session_update(self, session_id: str, update: object) -> None:
        self.updates.append((session_id, update))


class FakeSessions:
    def __init__(self) -> None:
        self.messages: dict[str, tuple[Message, ...]] = {}

    async def create_thread(self) -> str:
        self.messages["thread-new"] = ()
        return "thread-new"

    async def load_messages(self, session_id: str) -> tuple[Message, ...]:
        try:
            return self.messages[session_id]
        except KeyError:
            raise SessionNotFound("missing") from None

    async def list_threads(self, *, limit: int) -> tuple[object, ...]:
        assert limit == 1_000
        now = datetime(2026, 9, 2, tzinfo=timezone.utc)
        return (
            SimpleNamespace(
                id="thread-existing", title="Existing", updated_at=now
            ),
        )


class FakeController:
    def __init__(self, events: tuple[AgentEvent, ...]) -> None:
        self.events = events
        self.calls: list[tuple[str, str, object]] = []

    async def ask(
        self, text: str, *, thread_id: str, cancellation: object
    ):
        self.calls.append((text, thread_id, cancellation))
        for event in self.events:
            yield event


def _model_event(kind: ModelEventKind, text: str | None = None) -> AgentEvent:
    model = ModelEvent(kind, text=text)
    return AgentEvent(EventKind.MODEL_EVENT, {"event": model.to_dict()})


class ChaosAcpAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.root = Path.cwd().resolve()
        self.sessions = FakeSessions()
        self.sessions.messages["thread-existing"] = ()
        self.client = FakeClient()

    def agent(self, controller: object | None = None) -> ChaosAcpAgent:
        result = ChaosAcpAgent(
            controller or FakeController(()),
            self.sessions,
            self.root,
            version="1.0.3",
        )
        result.on_connect(self.client)
        return result

    async def test_initialize_advertises_supported_v1_capabilities(self) -> None:
        response = await self.agent().initialize(PROTOCOL_VERSION)

        self.assertEqual(response.protocol_version, PROTOCOL_VERSION)
        self.assertTrue(response.agent_capabilities.load_session)
        self.assertFalse(response.agent_capabilities.prompt_capabilities.image)
        self.assertIsNotNone(response.agent_capabilities.session_capabilities.list)
        self.assertEqual(response.agent_info.name, "chaos-agent")

    async def test_initialize_rejects_other_protocol_versions(self) -> None:
        with self.assertRaises(RequestError) as raised:
            await self.agent().initialize(PROTOCOL_VERSION + 1)

        self.assertEqual(raised.exception.code, -32602)

    async def test_new_and_list_sessions_use_existing_repository(self) -> None:
        agent = self.agent()

        created = await agent.new_session(str(self.root))
        listed = await agent.list_sessions(str(self.root))

        self.assertEqual(created.session_id, "thread-new")
        self.assertEqual(listed.sessions[0].session_id, "thread-existing")
        self.assertEqual(listed.sessions[0].cwd, str(self.root))

    async def test_session_setup_rejects_other_roots_and_client_mcp(self) -> None:
        agent = self.agent()

        with self.assertRaises(RequestError):
            await agent.new_session(str(self.root.parent))
        with self.assertRaises(RequestError):
            await agent.new_session(str(self.root), mcp_servers=[object()])

    async def test_load_session_replays_user_and_assistant_text(self) -> None:
        self.sessions.messages["thread-existing"] = (
            Message("user", "hello"),
            Message("tool", "ignored", name="read_file", tool_call_id="call-1"),
            Message("assistant", "world"),
        )

        await self.agent().load_session(str(self.root), "thread-existing")

        self.assertEqual(
            [update.session_update for _, update in self.client.updates],
            ["user_message_chunk", "agent_message_chunk"],
        )

    async def test_prompt_streams_text_thought_and_tool_lifecycle(self) -> None:
        request = ActionRequest("call-1", "read_file", {"path": "README.md"})
        result = ActionResult("call-1", "read_file", {"text": "ok"})
        controller = FakeController(
            (
                _model_event(ModelEventKind.REASONING_DELTA, "thinking"),
                _model_event(ModelEventKind.TEXT_DELTA, "answer"),
                AgentEvent(EventKind.ACTION_REQUESTED, {"request": request.to_dict()}),
                AgentEvent(
                    EventKind.ACTION_STARTED,
                    {"request_id": "call-1", "name": "read_file"},
                ),
                AgentEvent(EventKind.ACTION_COMPLETED, {"result": result.to_dict()}),
                AgentEvent(EventKind.COMPLETED, {"thread_id": "thread-existing"}),
            )
        )
        response = await self.agent(controller).prompt(
            "thread-existing",
            [
                schema.TextContentBlock(type="text", text="inspect"),
                schema.ResourceContentBlock(
                    type="resource_link", name="notes", uri="file:///notes.md"
                ),
            ],
        )

        self.assertEqual(response.stop_reason, "end_turn")
        self.assertEqual(controller.calls[0][0], "inspect\n\n[Resource link: notes] file:///notes.md")
        self.assertEqual(
            [update.session_update for _, update in self.client.updates],
            [
                "agent_thought_chunk",
                "agent_message_chunk",
                "tool_call",
                "tool_call_update",
                "tool_call_update",
            ],
        )
        self.assertEqual(self.client.updates[-1][1].status, "completed")

    async def test_prompt_rejects_unsupported_content(self) -> None:
        block = schema.ImageContentBlock(
            type="image", data="AA==", mimeType="image/png"
        )
        with self.assertRaises(RequestError) as raised:
            await self.agent().prompt("thread-existing", [block])

        self.assertEqual(raised.exception.code, -32602)

    async def test_prompt_requires_a_terminal_agent_event(self) -> None:
        with self.assertRaises(RequestError) as raised:
            await self.agent().prompt(
                "thread-existing",
                [schema.TextContentBlock(type="text", text="inspect")],
            )

        self.assertEqual(raised.exception.code, -32603)

    async def test_cancel_reaches_active_chaos_run(self) -> None:
        class WaitingController:
            def __init__(self) -> None:
                self.started = asyncio.Event()

            async def ask(self, text: str, *, thread_id: str, cancellation: object):
                del text, thread_id
                self.started.set()
                await cancellation.wait_async()
                yield AgentEvent(EventKind.CANCELLED, {"reason": "cancelled"})

        controller = WaitingController()
        agent = self.agent(controller)
        task = asyncio.create_task(
            agent.prompt(
                "thread-existing",
                [schema.TextContentBlock(type="text", text="wait")],
            )
        )
        await controller.started.wait()

        await agent.cancel("thread-existing")
        response = await task

        self.assertEqual(response.stop_reason, "cancelled")


if __name__ == "__main__":
    unittest.main()
