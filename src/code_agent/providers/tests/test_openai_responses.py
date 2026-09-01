from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.models import (  # noqa: E402
    Message, ModelEvent, ModelEventKind, ToolCall, ToolDefinition, Usage,
)
from code_agent.providers.config import ApiProtocol, ProviderConfig  # noqa: E402
from code_agent.providers.errors import ProviderProtocolError  # noqa: E402
from code_agent.providers.openai_responses import OpenAIResponsesClient  # noqa: E402


def event(name: str, data: dict[str, object]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


class OpenAIResponsesClientTests(unittest.IsolatedAsyncioTestCase):
    def make_config(self) -> ProviderConfig:
        return ProviderConfig(
            base_url="https://api.example.test", model="responses-model",
            api=ApiProtocol.RESPONSES, api_key_env="RESPONSES_KEY",
            max_retries=0, max_event_bytes=8192,
        )

    async def test_stream_aggregates_items_by_item_and_call_id_once(self) -> None:
        parts = [
            event("response.output_text.delta", {"type": "response.output_text.delta", "delta": "Hello"}),
            event("response.reasoning_summary_text.delta", {"type": "response.reasoning_summary_text.delta", "delta": "Plan"}),
            event("response.output_item.added", {"type": "response.output_item.added", "output_index": 0,
                "item": {"type": "function_call", "id": "fc-1", "call_id": "call-1", "name": "search", "arguments": ""}}),
            event("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta", "item_id": "fc-1", "delta": "{\"q\":"}),
            event("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta", "call_id": "call-1", "delta": "\"one\"}"}),
            event("response.output_item.done", {"type": "response.output_item.done", "output_index": 0,
                "item": {"type": "function_call", "id": "fc-1", "call_id": "call-1", "name": "search", "arguments": "{\"q\":\"one\"}"}}),
            event("response.output_item.done", {"type": "response.output_item.done",
                "item": {"type": "function_call", "id": "fc-1", "call_id": "call-1", "name": "search", "arguments": "{\"q\":\"one\"}"}}),
            event("response.output_item.added", {"type": "response.output_item.added", "output_index": 1,
                "item": {"type": "function_call", "call_id": "call-2", "name": "list_files", "arguments": "{}"}}),
            event("response.output_item.done", {"type": "response.output_item.done", "output_index": 1,
                "item": {"type": "function_call", "call_id": "call-2", "name": "list_files", "arguments": "{}"}}),
            event("response.unknown", {"type": "response.unknown", "value": "ignored"}),
            event("response.completed", {"type": "response.completed", "response": {"usage": {
                "input_tokens": 20, "output_tokens": 8,
                "input_tokens_details": {"cached_tokens": 6},
            }}}),
        ]
        content = b"".join(parts)
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, content=content)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = OpenAIResponsesClient(
            self.make_config(),
            http_client=http_client,
            reasoning_effort="max",
            max_output_tokens=23_456,
        )
        old_call = ToolCall(id="old-call", name="search", arguments={"q": "old"})
        messages = (
            Message(role="user", content="question"),
            Message(role="assistant", content="", tool_calls=(old_call,)),
            Message(role="tool", content="answer", tool_call_id="old-call"),
        )
        tools = (ToolDefinition(name="search", description="Search.", parameters={"type": "object"}),)
        with patch.dict("os.environ", {"RESPONSES_KEY": "key"}, clear=True):
            events = [event_ async for event_ in client.stream("System", messages, tools)]

        self.assertEqual(events, [
            ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="Hello"),
            ModelEvent(kind=ModelEventKind.REASONING_DELTA, text="Plan"),
            ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=ToolCall(id="call-1", name="search", arguments={"q": "one"})),
            ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=ToolCall(id="call-2", name="list_files", arguments={})),
            ModelEvent(kind=ModelEventKind.USAGE, usage=Usage(input_tokens=20, output_tokens=8, cached_input_tokens=6)),
            ModelEvent(kind=ModelEventKind.COMPLETED),
        ])
        body = json.loads(seen[0].read())
        self.assertEqual(body["instructions"], "System")
        self.assertEqual(body["reasoning"], {"effort": "max"})
        self.assertEqual(body["max_output_tokens"], 23_456)
        self.assertEqual(body["input"][1]["type"], "function_call")
        self.assertEqual(body["input"][2], {"type": "function_call_output", "call_id": "old-call", "output": "answer"})
        self.assertEqual(body["tools"][0]["parameters"], {"type": "object"})
        await http_client.aclose()

    async def test_error_event_raises_without_exposing_body(self) -> None:
        content = event("error", {"type": "error", "error": {"message": "sensitive body"}})

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=content)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = OpenAIResponsesClient(self.make_config(), http_client=http_client)
        with patch.dict("os.environ", {"RESPONSES_KEY": "key"}, clear=True):
            with self.assertRaises(ProviderProtocolError) as raised:
                _ = [item async for item in client.stream("", (), ())]
        self.assertNotIn("sensitive body", str(raised.exception))
        await http_client.aclose()

    async def test_failed_and_incomplete_are_immediate_bounded_errors(self) -> None:
        terminal_events = (
            (
                "response.failed",
                {
                    "type": "response.failed",
                    "response": {
                        "error": {
                            "code": "server_error",
                            "message": (
                                "upstream failed; Authorization: Basic c2VjcmV0; "
                                + "x" * 2000
                            ),
                        }
                    },
                },
                "server_error",
            ),
            (
                "response.incomplete",
                {
                    "type": "response.incomplete",
                    "response": {
                        "incomplete_details": {"reason": "max_output_tokens"}
                    },
                },
                "max_output_tokens",
            ),
        )
        completed = event(
            "response.completed",
            {"type": "response.completed", "response": {}},
        )

        for event_name, payload, expected_summary in terminal_events:
            with self.subTest(event_name=event_name):
                content = event(event_name, payload) + completed

                async def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(200, content=content)

                http_client = httpx.AsyncClient(
                    transport=httpx.MockTransport(handler)
                )
                client = OpenAIResponsesClient(
                    self.make_config(), http_client=http_client
                )
                received: list[ModelEvent] = []
                with patch.dict(
                    "os.environ", {"RESPONSES_KEY": "key"}, clear=True
                ):
                    with self.assertRaises(ProviderProtocolError) as raised:
                        async for item in client.stream("", (), ()):
                            received.append(item)

                rendered = str(raised.exception)
                self.assertIn(event_name, rendered)
                self.assertIn(expected_summary, rendered)
                self.assertNotIn("c2VjcmV0", rendered)
                self.assertLessEqual(len(rendered), 320)
                self.assertFalse(
                    any(item.kind is ModelEventKind.COMPLETED for item in received)
                )
                await http_client.aclose()


if __name__ == "__main__":
    unittest.main()
