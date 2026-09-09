from __future__ import annotations

import json
import hashlib
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from code_agent.core.models import Message, ModelEventKind, ToolCall, ToolDefinition
from code_agent.core.attachments import AttachmentRef
from code_agent.providers.config import ApiProtocol, ProviderConfig, ConfiguredApiKey, InputModality
from code_agent.providers.errors import ProviderConfigError, ProviderProtocolError, ProviderResponseLimitError
from code_agent.providers.google_generative_ai import GoogleGenerativeAIClient
from code_agent.providers.google_generative_ai import _thinking
from code_agent.providers._request_payload import ProviderRequestOptions
from code_agent.providers.pi_messages import PiMessagesClient


def sse(*values):
    return "".join(f"data: {json.dumps(value)}\n\n" for value in values).encode()


class AdditionalProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_google_reasoning_family_mapping_and_output_limit(self):
        options = ProviderRequestOptions("medium", 16384)
        self.assertEqual(_thinking("gemini-3.1-pro", options)["thinkingLevel"], "HIGH")
        self.assertEqual(_thinking("gemini-3-flash", options)["thinkingLevel"], "MEDIUM")
        self.assertEqual(_thinking("gemini-2.5-pro", options)["thinkingBudget"], 8192)
        for model, value in (("unknown", options), ("gemini-2.5-pro", ProviderRequestOptions("high", 4096))):
            with self.subTest(model=model), self.assertRaises(ProviderConfigError):
                _thinking(model, value)

    async def run_stream(self, api, values, messages=(), **limits):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, content=sse(*values))
        config = ProviderConfig(base_url="https://example.test/v1beta", model="test-model", api=api,
                                api_key_source=ConfiguredApiKey("test-key"), **limits)
        factory = GoogleGenerativeAIClient if api is ApiProtocol.GOOGLE_GENERATIVE_AI else PiMessagesClient
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = factory(config, http_client=http)
            events = [event async for event in client.stream("system", messages, [])]
        return seen, events

    async def test_google_wire_text_reasoning_tools_usage_and_history(self):
        messages = [Message(role="assistant", tool_calls=(ToolCall(id="a", name="read", arguments={}),)),
                    Message(role="tool", tool_call_id="a", content="result")]
        seen, events = await self.run_stream(ApiProtocol.GOOGLE_GENERATIVE_AI, [
            {"candidates": [{"content": {"parts": [{"text": "plan", "thought": True}, {"text": "answer"},
                {"functionCall": {"name": "read", "args": {"path": "x"}}}]}}]},
            {"candidates": [{"finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 8,
                "candidatesTokenCount": 2, "thoughtsTokenCount": 1, "cachedContentTokenCount": 3}},
        ], messages)
        self.assertEqual([event.kind for event in events], [ModelEventKind.REASONING_DELTA,
            ModelEventKind.TEXT_DELTA, ModelEventKind.TOOL_CALL, ModelEventKind.USAGE, ModelEventKind.COMPLETED])
        self.assertEqual(events[-2].usage.output_tokens, 3)
        request = seen[0]
        self.assertEqual(str(request.url), "https://example.test/v1beta/models/test-model:streamGenerateContent?alt=sse")
        self.assertEqual(request.headers["x-goog-api-key"], "test-key")
        body = json.loads(request.content)
        self.assertEqual(body["contents"][1]["parts"][0]["functionResponse"]["name"], "read")

    async def test_pi_wire_history_and_tool_events(self):
        messages = [Message(role="assistant", tool_calls=(ToolCall(id="a", name="read", arguments={}),)),
                    Message(role="tool", tool_call_id="a", content="result")]
        seen, events = await self.run_stream(ApiProtocol.PI_MESSAGES, [
            {"type": "text_delta", "contentIndex": 0, "delta": "answer"},
            {"type": "toolcall_start", "contentIndex": 1},
            {"type": "toolcall_delta", "contentIndex": 1, "delta": "{}"},
            {"type": "toolcall_end", "contentIndex": 1, "toolCall": {"id": "b", "name": "read", "arguments": {}}},
            {"type": "done", "reason": "toolUse", "usage": {"input": 3, "output": 2, "cacheRead": 1}},
        ], messages)
        body = json.loads(seen[0].content)
        self.assertEqual(body["context"]["messages"][1]["role"], "toolResult")
        self.assertEqual(body["context"]["messages"][1]["toolName"], "read")
        self.assertEqual(events[1].tool_call.id, "b")
        self.assertEqual(events[-1].kind, ModelEventKind.COMPLETED)

    async def test_pi_unfinished_tool_rejected(self):
        with self.assertRaises(ProviderProtocolError):
            await self.run_stream(ApiProtocol.PI_MESSAGES, [
                {"type": "toolcall_start", "contentIndex": 0}, {"type": "done", "reason": "stop"}])

    async def test_pi_cross_block_delta_rejected(self):
        with self.assertRaises(ProviderProtocolError):
            await self.run_stream(ApiProtocol.PI_MESSAGES, [
                {"type": "text_start", "contentIndex": 0},
                {"type": "thinking_delta", "contentIndex": 0, "delta": "bad"}])

    async def test_google_truncation_not_completed(self):
        with self.assertRaises(ProviderProtocolError):
            await self.run_stream(ApiProtocol.GOOGLE_GENERATIVE_AI, [{"candidates": [{"finishReason": "MAX_TOKENS"}]}])

    async def test_google_tool_budget_enforced(self):
        with self.assertRaises(ProviderResponseLimitError):
            await self.run_stream(ApiProtocol.GOOGLE_GENERATIVE_AI, [{"candidates": [{"content": {"parts": [
                {"functionCall": {"name": "read", "args": {"large": "x" * 50}}}]}}]}], max_tool_argument_bytes=10)

    async def test_google_malformed_shapes_usage_and_multiple_candidates(self):
        for value in ({"candidates": [None]}, {"candidates": [{}, {}]},
                      {"usageMetadata": {"candidatesTokenCount": True}},
                      {"candidates": [{"content": {"parts": [None]}}]}):
            with self.subTest(value=value), self.assertRaises(ProviderProtocolError):
                await self.run_stream(ApiProtocol.GOOGLE_GENERATIVE_AI, [value])

    async def test_pi_duplicate_tool_end_and_negative_usage_rejected(self):
        call = {"type": "toolcall_end", "contentIndex": 0,
                "toolCall": {"id": "a", "name": "read", "arguments": {}}}
        for values in ([call, call], [{"type": "done", "reason": "stop", "usage": {"input": -1}}]):
            with self.subTest(values=values), self.assertRaises(ProviderProtocolError):
                await self.run_stream(ApiProtocol.PI_MESSAGES, values)

    async def test_both_protocols_encode_images_and_fail_closed_without_capability(self):
        data = b"normalized-image"
        ref = AttachmentRef(hashlib.sha256(data).hexdigest(), "image/png", len(data), "image.png", 1, 1)
        class Resolver:
            def read(self, reference):
                return data
        for api, factory, terminal in (
            (ApiProtocol.GOOGLE_GENERATIVE_AI, GoogleGenerativeAIClient, {"candidates": [{"finishReason": "STOP"}]}),
            (ApiProtocol.PI_MESSAGES, PiMessagesClient, {"type": "done", "reason": "stop"}),
        ):
            seen = []
            def handler(request):
                seen.append(json.loads(request.content))
                return httpx.Response(200, content=sse(terminal))
            config = ProviderConfig("https://example.test", "model", api, api_key_source=ConfiguredApiKey("key"))
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                for modalities in ((InputModality.TEXT,), (InputModality.TEXT, InputModality.IMAGE)):
                    client = factory(config, http_client=http, attachment_resolver=Resolver(), input_modalities=modalities)
                    if len(modalities) == 1:
                        with self.assertRaises(ProviderConfigError):
                            _ = [e async for e in client.stream("", [Message("user", attachments=(ref,))], [])]
                        self.assertEqual(seen, [])
                    else:
                        _ = [e async for e in client.stream("", [Message("user", attachments=(ref,))], [])]
            body = seen[0]
            if api is ApiProtocol.GOOGLE_GENERATIVE_AI:
                self.assertEqual(body["contents"][0]["parts"][0]["inlineData"]["mimeType"], "image/png")
            else:
                self.assertEqual(body["context"]["messages"][0]["content"][0]["mimeType"], "image/png")


if __name__ == "__main__":
    unittest.main()
