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

from code_agent.providers.anthropic import AnthropicClient  # noqa: E402
from code_agent.providers.config import ApiProtocol, ProviderConfig  # noqa: E402
from code_agent.providers.errors import ProviderProtocolError  # noqa: E402
from code_agent.providers.openai_chat import OpenAIChatClient  # noqa: E402
from code_agent.providers.openai_responses import OpenAIResponsesClient  # noqa: E402


def sse(data: object, event_name: str | None = None) -> bytes:
    encoded = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
    prefix = f"event: {event_name}\n" if event_name else ""
    return f"{prefix}data: {encoded}\n\n".encode()


def config(api: ApiProtocol, key_env: str) -> ProviderConfig:
    return ProviderConfig(
        base_url="https://api.example.test",
        model="model-1",
        api=api,
        api_key_env=key_env,
        max_retries=0,
        max_event_bytes=16_384,
    )


class AdapterSafeCauseTests(unittest.IsolatedAsyncioTestCase):
    async def assert_safe_failure(
        self,
        adapter: object,
        http_client: httpx.AsyncClient,
        key_env: str,
        key: str,
    ) -> None:
        with patch.dict("os.environ", {key_env: key}, clear=True):
            with self.assertRaises(ProviderProtocolError) as raised:
                _ = [
                    item
                    async for item in adapter.stream("", (), ())  # type: ignore[attr-defined]
                ]

        error = raised.exception
        self.assertNotIsInstance(error.__cause__, json.JSONDecodeError)
        for rendered in (str(error), repr(error)):
            self.assertNotIn(key, rendered)
            self.assertNotIn("x" * 64, rendered)
        await http_client.aclose()

    async def test_malformed_stream_json_never_chains_large_document(self) -> None:
        key = "safe-cause-api-key"
        malformed = '{"api_key":"' + key + '","pad":"' + "x" * 2048
        cases = (
            (OpenAIChatClient, ApiProtocol.CHAT_COMPLETIONS, "CHAT_SAFE_KEY", sse(malformed)),
            (
                OpenAIResponsesClient,
                ApiProtocol.RESPONSES,
                "RESPONSES_SAFE_KEY",
                sse(malformed, "response.output_text.delta"),
            ),
            (
                AnthropicClient,
                ApiProtocol.ANTHROPIC_MESSAGES,
                "ANTHROPIC_SAFE_KEY",
                sse(malformed, "message_start"),
            ),
        )

        for client_type, api, key_env, content in cases:
            with self.subTest(api=api):
                async def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(200, content=content)

                http_client = httpx.AsyncClient(
                    transport=httpx.MockTransport(handler)
                )
                adapter = client_type(config(api, key_env), http_client=http_client)
                await self.assert_safe_failure(adapter, http_client, key_env, key)

    async def test_malformed_tool_json_never_chains_large_document(self) -> None:
        key = "safe-tool-api-key"
        malformed = '{"api_key":"' + key + '","pad":"' + "x" * 2048
        chat = sse(
            {
                "choices": [
                    {
                        "delta": {"tool_calls": [{
                            "index": 0,
                            "id": "call-1",
                            "function": {
                                "name": "read_file",
                                "arguments": malformed,
                            },
                        }]},
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
        responses = b"".join((
            sse({
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "read_file",
                    "arguments": malformed,
                },
            }, "response.output_item.added"),
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
                "delta": {"type": "input_json_delta", "partial_json": malformed},
            }, "content_block_delta"),
            sse({"type": "content_block_stop", "index": 0}, "content_block_stop"),
        ))
        cases = (
            (OpenAIChatClient, ApiProtocol.CHAT_COMPLETIONS, "CHAT_TOOL_KEY", chat),
            (OpenAIResponsesClient, ApiProtocol.RESPONSES, "RESPONSES_TOOL_KEY", responses),
            (AnthropicClient, ApiProtocol.ANTHROPIC_MESSAGES, "ANTHROPIC_TOOL_KEY", anthropic),
        )

        for client_type, api, key_env, content in cases:
            with self.subTest(api=api):
                async def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(200, content=content)

                http_client = httpx.AsyncClient(
                    transport=httpx.MockTransport(handler)
                )
                adapter = client_type(config(api, key_env), http_client=http_client)
                await self.assert_safe_failure(adapter, http_client, key_env, key)


if __name__ == "__main__":
    unittest.main()
