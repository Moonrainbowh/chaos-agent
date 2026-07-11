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
    Message,
    ModelEvent,
    ModelEventKind,
    ToolCall,
    ToolDefinition,
    Usage,
)
from code_agent.providers.config import ApiProtocol, ProviderConfig  # noqa: E402
from code_agent.providers.errors import ProviderProtocolError  # noqa: E402
from code_agent.providers.openai_chat import OpenAIChatClient  # noqa: E402


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self._chunks = [content[index : index + 7] for index in range(0, len(content), 7)]

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


def sse(data: object) -> bytes:
    encoded = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
    return f"data: {encoded}\r\n\r\n".encode()


class OpenAIChatClientTests(unittest.IsolatedAsyncioTestCase):
    def make_config(self) -> ProviderConfig:
        return ProviderConfig(
            base_url="https://api.example.test",
            model="chat-model",
            api=ApiProtocol.CHAT_COMPLETIONS,
            api_key_env="CHAT_KEY",
            max_retries=0,
            max_event_bytes=4096,
        )

    async def test_stream_serializes_request_and_aggregates_multiple_tools(self) -> None:
        content = b"".join(
            (
                sse({"choices": [{"delta": {"content": "Hi", "reasoning_content": "think"}}]}),
                sse({"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "id": "call_", "function": {"name": "read_", "arguments": "{\"pa"}},
                    {"index": 1, "id": "call_", "function": {"name": "list_", "arguments": "{"}},
                ]}}]}),
                sse({"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "id": "1", "function": {"name": "file", "arguments": "th\":\"a.txt\"}"}},
                    {"index": 1, "id": "2", "function": {"name": "files", "arguments": "}"}},
                ]}, "finish_reason": "tool_calls"}], "usage": {
                    "prompt_tokens": 12, "completion_tokens": 5,
                    "prompt_tokens_details": {"cached_tokens": 3},
                }}),
                sse("[DONE]"),
            )
        )
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, stream=ChunkStream(content))

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = OpenAIChatClient(self.make_config(), http_client=http_client)
        previous = ToolCall(id="old-1", name="read_file", arguments={"path": "old.txt"})
        messages = (
            Message(role="user", content="hello"),
            Message(role="assistant", content="working", tool_calls=(previous,)),
            Message(role="tool", content="old result", tool_call_id="old-1"),
        )
        tools = (ToolDefinition(
            name="read_file",
            description="Read a file.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        ),)

        with patch.dict("os.environ", {"CHAT_KEY": "chat-secret"}, clear=True):
            events = [event async for event in client.stream("Be precise.", messages, tools)]

        self.assertEqual(events, [
            ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="Hi"),
            ModelEvent(kind=ModelEventKind.REASONING_DELTA, text="think"),
            ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=ToolCall(
                id="call_1", name="read_file", arguments={"path": "a.txt"},
            )),
            ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=ToolCall(
                id="call_2", name="list_files", arguments={},
            )),
            ModelEvent(kind=ModelEventKind.USAGE, usage=Usage(
                input_tokens=12, output_tokens=5, cached_input_tokens=3,
            )),
            ModelEvent(kind=ModelEventKind.COMPLETED),
        ])
        body = json.loads(requests[0].read())
        self.assertEqual(body["model"], "chat-model")
        self.assertTrue(body["stream"])
        self.assertEqual(body["messages"][0], {"role": "system", "content": "Be precise."})
        self.assertEqual(body["messages"][2]["tool_calls"][0]["function"]["arguments"], '{"path":"old.txt"}')
        self.assertEqual(body["messages"][3]["tool_call_id"], "old-1")
        self.assertEqual(body["tools"][0]["function"]["name"], "read_file")
        await client.aclose()
        self.assertFalse(http_client.is_closed)
        await http_client.aclose()

    async def test_malformed_tool_arguments_raise_protocol_error(self) -> None:
        content = sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call-1", "function": {"name": "read_file", "arguments": "[]"}},
        ]}, "finish_reason": "tool_calls"}]})

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=content)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = OpenAIChatClient(self.make_config(), http_client=http_client)
        with patch.dict("os.environ", {"CHAT_KEY": "key"}, clear=True):
            with self.assertRaises(ProviderProtocolError):
                _ = [event async for event in client.stream("", (), ())]
        await http_client.aclose()

    async def test_sse_error_event_stops_before_following_done(self) -> None:
        content = (
            b'event: error\ndata: {"message":"private response body"}\n\n'
            b"data: [DONE]\n\n"
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=content)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = OpenAIChatClient(self.make_config(), http_client=http_client)
        events: list[ModelEvent] = []
        with patch.dict("os.environ", {"CHAT_KEY": "key"}, clear=True):
            with self.assertRaises(ProviderProtocolError) as raised:
                async for item in client.stream("", (), ()):
                    events.append(item)

        self.assertFalse(
            any(item.kind is ModelEventKind.COMPLETED for item in events)
        )
        self.assertNotIn("private response body", str(raised.exception))
        await http_client.aclose()

    async def test_delta_after_finish_is_rejected_without_duplicate_completion(self) -> None:
        first = sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": "{}",
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
        replay_deltas = (
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-1",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "index": 1,
                        "id": "call-2",
                        "function": {"name": "list_files", "arguments": "{}"},
                    }
                ]
            },
            {"content": "late text"},
        )

        for replay in replay_deltas:
            with self.subTest(replay=replay):
                content = first + sse(
                    {"choices": [{"delta": replay, "finish_reason": None}]}
                ) + sse("[DONE]")

                async def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(200, content=content)

                http_client = httpx.AsyncClient(
                    transport=httpx.MockTransport(handler)
                )
                client = OpenAIChatClient(
                    self.make_config(), http_client=http_client
                )
                received: list[ModelEvent] = []
                with patch.dict("os.environ", {"CHAT_KEY": "key"}, clear=True):
                    with self.assertRaises(ProviderProtocolError):
                        async for item in client.stream("", (), ()):
                            received.append(item)

                tool_events = [
                    item for item in received if item.kind is ModelEventKind.TOOL_CALL
                ]
                self.assertEqual(len(tool_events), 1)
                self.assertEqual(tool_events[0].tool_call.id, "call-1")  # type: ignore[union-attr]
                self.assertFalse(
                    any(item.kind is ModelEventKind.COMPLETED for item in received)
                )
                await http_client.aclose()


if __name__ == "__main__":
    unittest.main()
