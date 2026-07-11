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

from code_agent.core.models import ModelEvent, ModelEventKind  # noqa: E402
from code_agent.providers.anthropic import AnthropicClient  # noqa: E402
from code_agent.providers.config import ApiProtocol, ProviderConfig  # noqa: E402
from code_agent.providers.errors import ProviderResponseLimitError  # noqa: E402
from code_agent.providers.openai_chat import OpenAIChatClient  # noqa: E402
from code_agent.providers.openai_responses import OpenAIResponsesClient  # noqa: E402


def sse(data: dict[str, object] | str, event_name: str | None = None) -> bytes:
    encoded = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
    prefix = f"event: {event_name}\n" if event_name else ""
    return f"{prefix}data: {encoded}\n\n".encode()


def make_config(
    api: ApiProtocol,
    key_env: str,
    *,
    max_argument_bytes: int = 100,
    max_calls: int = 4,
) -> ProviderConfig:
    return ProviderConfig(
        base_url="https://api.example.test",
        model="model-1",
        api=api,
        api_key_env=key_env,
        max_retries=0,
        max_event_bytes=8192,
        max_response_bytes=32_768,
        max_tool_argument_bytes=max_argument_bytes,
        max_tool_calls=max_calls,
    )


class AdapterLimitTests(unittest.IsolatedAsyncioTestCase):
    async def assert_limit_failure(
        self,
        client_type: type,
        api: ApiProtocol,
        key_env: str,
        content: bytes,
        *,
        max_argument_bytes: int = 100,
        max_calls: int = 4,
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=content)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = client_type(
            make_config(
                api,
                key_env,
                max_argument_bytes=max_argument_bytes,
                max_calls=max_calls,
            ),
            http_client=http_client,
        )
        received: list[ModelEvent] = []
        with patch.dict("os.environ", {key_env: "key"}, clear=True):
            with self.assertRaises(ProviderResponseLimitError):
                async for item in adapter.stream("", (), ()):
                    received.append(item)

        forbidden = {ModelEventKind.TOOL_CALL, ModelEventKind.COMPLETED}
        self.assertFalse(any(item.kind in forbidden for item in received))
        await http_client.aclose()

    async def test_utf8_tool_arguments_are_bounded_across_events(self) -> None:
        first_fragment = '{"x":"'
        second_fragment = '你"}'
        chat = b"".join((
            sse({"choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "call-1",
                "function": {
                    "name": "read_file",
                    "arguments": first_fragment,
                },
            }]}, "finish_reason": None}]}),
            sse({"choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "function": {"arguments": second_fragment},
            }]}, "finish_reason": "tool_calls"}]}),
            sse("[DONE]"),
        ))
        responses = b"".join((
            sse({
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "read_file",
                    "arguments": "",
                },
            }, "response.output_item.added"),
            sse({
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "delta": first_fragment,
            }, "response.function_call_arguments.delta"),
            sse({
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "delta": second_fragment,
            }, "response.function_call_arguments.delta"),
            sse({"type": "response.completed", "response": {}}, "response.completed"),
        ))
        anthropic = b"".join((
            sse({
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "read_file",
                    "input": {},
                },
            }, "content_block_start"),
            sse({
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": first_fragment},
            }, "content_block_delta"),
            sse({
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": second_fragment},
            }, "content_block_delta"),
            sse({"type": "content_block_stop", "index": 0}, "content_block_stop"),
            sse({"type": "message_stop"}, "message_stop"),
        ))
        cases = (
            (OpenAIChatClient, ApiProtocol.CHAT_COMPLETIONS, "CHAT_LIMIT", chat),
            (OpenAIResponsesClient, ApiProtocol.RESPONSES, "RESP_LIMIT", responses),
            (AnthropicClient, ApiProtocol.ANTHROPIC_MESSAGES, "ANTH_LIMIT", anthropic),
        )

        for client_type, api, key_env, content in cases:
            with self.subTest(api=api):
                await self.assert_limit_failure(
                    client_type, api, key_env, content, max_argument_bytes=9
                )

    async def test_tool_call_count_is_bounded_across_events(self) -> None:
        chat_parts = []
        response_parts = []
        anthropic_parts = []
        for index in range(2):
            chat_parts.append(sse({"choices": [{"delta": {"tool_calls": [{
                "index": index,
                "id": f"call-{index}",
                "function": {"name": "read_file", "arguments": "{}"},
            }]}, "finish_reason": None}]}))
            response_parts.append(sse({
                "type": "response.output_item.added",
                "output_index": index,
                "item": {
                    "type": "function_call",
                    "call_id": f"call-{index}",
                    "name": "read_file",
                    "arguments": "{}",
                },
            }, "response.output_item.added"))
            anthropic_parts.append(sse({
                "type": "content_block_start",
                "index": index,
                "content_block": {
                    "type": "tool_use",
                    "id": f"tool-{index}",
                    "name": "read_file",
                    "input": {},
                },
            }, "content_block_start"))
        chat_parts.append(sse("[DONE]"))
        response_parts.append(
            sse({"type": "response.completed", "response": {}}, "response.completed")
        )
        anthropic_parts.append(sse({"type": "message_stop"}, "message_stop"))
        cases = (
            (OpenAIChatClient, ApiProtocol.CHAT_COMPLETIONS, "CHAT_COUNT", b"".join(chat_parts)),
            (OpenAIResponsesClient, ApiProtocol.RESPONSES, "RESP_COUNT", b"".join(response_parts)),
            (AnthropicClient, ApiProtocol.ANTHROPIC_MESSAGES, "ANTH_COUNT", b"".join(anthropic_parts)),
        )

        for client_type, api, key_env, content in cases:
            with self.subTest(api=api):
                await self.assert_limit_failure(
                    client_type, api, key_env, content, max_calls=1
                )


if __name__ == "__main__":
    unittest.main()
