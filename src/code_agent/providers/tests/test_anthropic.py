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
from code_agent.providers.anthropic import AnthropicClient  # noqa: E402
from code_agent.providers.config import ApiProtocol, ProviderConfig  # noqa: E402
from code_agent.providers.errors import ProviderProtocolError  # noqa: E402


def sse(name: str, data: dict[str, object]) -> bytes:
    return f"event: {name}\r\ndata: {json.dumps(data, separators=(',', ':'))}\r\n\r\n".encode()


class AnthropicClientTests(unittest.IsolatedAsyncioTestCase):
    def make_config(self) -> ProviderConfig:
        return ProviderConfig(
            base_url="https://api.example.test", model="claude-model",
            api=ApiProtocol.ANTHROPIC_MESSAGES, api_key_env="ANTHROPIC_KEY",
            max_retries=0, max_event_bytes=8192,
        )

    async def test_stream_serializes_anthropic_shape_and_tool_input(self) -> None:
        content = b"".join((
            sse("message_start", {"type": "message_start", "message": {"usage": {
                "input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 4,
            }}}),
            sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": "Hello"}}),
            sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "Reason"}}),
            sse("content_block_start", {"type": "content_block_start", "index": 1,
                "content_block": {"type": "tool_use", "id": "tool-1", "name": "read_file", "input": {}}}),
            sse("content_block_delta", {"type": "content_block_delta", "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": "{\"path\":"}}),
            sse("content_block_delta", {"type": "content_block_delta", "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": "\"a.txt\"}"}}),
            sse("content_block_stop", {"type": "content_block_stop", "index": 1}),
            sse("message_delta", {"type": "message_delta", "usage": {"output_tokens": 7}}),
            sse("message_stop", {"type": "message_stop"}),
        ))
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=content)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = AnthropicClient(self.make_config(), http_client=http_client)
        old = ToolCall(id="old-tool", name="read_file", arguments={"path": "old"})
        messages = (
            Message(role="user", content="question"),
            Message(role="assistant", content="calling", tool_calls=(old,)),
            Message(role="tool", content="result", tool_call_id="old-tool"),
        )
        tools = (ToolDefinition(name="read_file", description="Read.", parameters={"type": "object"}),)
        with patch.dict("os.environ", {"ANTHROPIC_KEY": "anthropic-secret"}, clear=True):
            events = [item async for item in client.stream("System", messages, tools)]

        self.assertEqual(events, [
            ModelEvent(kind=ModelEventKind.USAGE, usage=Usage(input_tokens=10, output_tokens=1, cached_input_tokens=4)),
            ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="Hello"),
            ModelEvent(kind=ModelEventKind.REASONING_DELTA, text="Reason"),
            ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=ToolCall(id="tool-1", name="read_file", arguments={"path": "a.txt"})),
            ModelEvent(kind=ModelEventKind.USAGE, usage=Usage(input_tokens=10, output_tokens=7, cached_input_tokens=4)),
            ModelEvent(kind=ModelEventKind.COMPLETED),
        ])
        request = requests[0]
        body = json.loads(request.read())
        self.assertEqual(request.headers["x-api-key"], "anthropic-secret")
        self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
        self.assertEqual(body["system"], "System")
        self.assertEqual(body["messages"][1]["content"][1]["type"], "tool_use")
        self.assertEqual(body["messages"][2]["content"][0]["type"], "tool_result")
        self.assertEqual(body["tools"][0]["input_schema"], {"type": "object"})
        await http_client.aclose()

    async def test_malformed_tool_input_and_error_event_are_safe_errors(self) -> None:
        cases = (
            b"".join((
                sse("content_block_start", {"type": "content_block_start", "index": 0,
                    "content_block": {"type": "tool_use", "id": "tool-1", "name": "read_file", "input": {}}}),
                sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": "[]"}}),
                sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
            )),
            sse("error", {"type": "error", "error": {"message": "private response body"}}),
        )
        for content in cases:
            with self.subTest(content=content[:20]):
                async def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(200, content=content)

                http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                client = AnthropicClient(self.make_config(), http_client=http_client)
                with patch.dict("os.environ", {"ANTHROPIC_KEY": "key"}, clear=True):
                    with self.assertRaises(ProviderProtocolError) as raised:
                        _ = [item async for item in client.stream("", (), ())]
                self.assertNotIn("private response body", str(raised.exception))
                await http_client.aclose()


if __name__ == "__main__":
    unittest.main()
