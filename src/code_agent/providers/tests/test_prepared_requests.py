"""Final protocol bodies are admitted and sent unchanged, without real network."""
import hashlib
import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from code_agent.authentication.models import Credential
from code_agent.core.attachments import AttachmentRef
from code_agent.core.models import Message, ToolCall, ToolDefinition
from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.context_windows.counting import PromptTokenCounter
from code_agent.context_windows.policy import ApiContextLimits, WindowPolicy, RequestBudgetConstraints
from code_agent.providers.config import ApiProtocol, ProviderConfig, ConfiguredApiKey, InputModality
from code_agent.providers.openai_chat import OpenAIChatClient
from code_agent.providers.openai_responses import OpenAIResponsesClient
from code_agent.providers.anthropic import AnthropicClient
from code_agent.providers.google_generative_ai import GoogleGenerativeAIClient
from code_agent.providers.pi_messages import PiMessagesClient


class Ledger:
    def __init__(self):
        self.reserved, self.settled = [], []

    async def reserve_context_call(self, *args):
        self.reserved.append(args)
        return "admission"

    async def settle_context_call(self, *args, **kwargs):
        self.settled.append((args, kwargs))


def response(api):
    events = {
        ApiProtocol.CHAT_COMPLETIONS: [
            {"choices": [{"delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 3, "completion_tokens": 1}}, "[DONE]"],
        ApiProtocol.RESPONSES: [{"type": "response.completed", "response": {
            "usage": {"input_tokens": 3, "output_tokens": 1}}}],
        ApiProtocol.ANTHROPIC_MESSAGES: [{"type": "message_start", "message": {
            "usage": {"input_tokens": 3, "output_tokens": 1}}}, {"type": "message_stop"}],
        ApiProtocol.GOOGLE_GENERATIVE_AI: [{"candidates": [{"content": {"parts": []},
            "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1}}],
        ApiProtocol.PI_MESSAGES: [{"type": "done", "reason": "stop",
            "usage": {"input": 3, "output": 1, "totalTokens": 4}}],
    }[api]
    return httpx.Response(200, content="".join("data: " + (e if isinstance(e, str) else json.dumps(e))
        + "\n\n" for e in events), headers={"content-type": "text/event-stream"})


class PreparedRequestTests(unittest.IsolatedAsyncioTestCase):
    def config(self, api, provider=None, base="https://offline.example.test", retries=0):
        return ProviderConfig(base, "offline", api, api_key_source=ConfiguredApiKey("private-fake-key"),
            provider_id=provider, max_retries=retries)

    def guarded(self, model, *, cap=100000, host=None, ledger=None):
        ledger = ledger or Ledger()
        return BudgetedWindowClient(model, ledger, lambda: "explicit-offline-thread",
            WindowPolicy(work_tokens=cap, safety_tokens=64, task_tokens=1_000_000),
            ApiContextLimits(cap + 128 + 64, 128), PromptTokenCounter(),
            constraints=RequestBudgetConstraints(host_prompt_tokens=host)), ledger

    async def test_all_protocols_prepared_bytes_equal_wire_once_and_main_aux_share_ledger(self):
        for api, factory in ((ApiProtocol.CHAT_COMPLETIONS, OpenAIChatClient),
            (ApiProtocol.RESPONSES, OpenAIResponsesClient),
            (ApiProtocol.ANTHROPIC_MESSAGES, AnthropicClient),
            (ApiProtocol.GOOGLE_GENERATIVE_AI, GoogleGenerativeAIClient),
            (ApiProtocol.PI_MESSAGES, PiMessagesClient)):
            with self.subTest(api=api):
                seen = []
                def handle(request):
                    seen.append(request.content)
                    return response(api)
                async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                    model = factory(self.config(api), http_client=http, max_output_tokens=128)
                    prepared = await model.prepare_request("系统", (Message("user", "你好"),),
                        (ToolDefinition("read_file", "中文 schema", {"type": "object"}),))
                    self.assertNotIn("private-fake-key", repr(prepared))
                    self.assertNotIn("private-fake-key", prepared.input_text)
                    _ = [e async for e in model.stream_prepared(prepared)]
                    self.assertEqual(seen, [prepared.body])
                    guarded, ledger = self.guarded(model)
                    _ = [e async for e in guarded.stream("系统", (Message("user", "你好"),), ())]
                    _ = [e async for e in guarded.stream_for("handoff", "摘要", (Message("user", "来源"),), ())]
                    self.assertEqual([r[-1] for r in ledger.reserved], ["main", "handoff"])
                    self.assertEqual(len(ledger.settled), 2)

    async def test_unicode_argument_and_large_actual_schema_fail_before_http_or_reserve(self):
        seen = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: seen.append(r))) as http:
            model = OpenAIChatClient(self.config(ApiProtocol.CHAT_COMPLETIONS), http_client=http, max_output_tokens=128)
            call = ToolCall("closed", "read_file", {"path": "中" * 3000})
            messages = (Message("assistant", "", tool_calls=(call,)), Message("tool", "ok", tool_call_id=call.id))
            counter = PromptTokenCounter()
            legacy = counter.request("system", messages, ())
            guarded, ledger = self.guarded(model, cap=legacy + 1)
            with self.assertRaisesRegex(ValueError, "final input"):
                _ = [e async for e in guarded.stream("system", messages, ())]
            small, ledger2 = self.guarded(model, cap=1000)
            with self.assertRaisesRegex(ValueError, "final input"):
                _ = [e async for e in small.stream("system", (Message("user", "ok"),),
                    (ToolDefinition("read", "schema" * 1000, {"type": "object"}),))]
            self.assertEqual((seen, ledger.reserved, ledger2.reserved), ([], [], []))

    async def test_host_and_auxiliary_caps_only_shrink_and_output_reserve_tracks_actual_client(self):
        seen = []
        def handle(request):
            seen.append(request)
            return response(ApiProtocol.CHAT_COMPLETIONS)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            model = OpenAIChatClient(self.config(ApiProtocol.CHAT_COMPLETIONS), http_client=http, max_output_tokens=512)
            guarded, ledger = self.guarded(model, host=500)
            for override in (None, RequestBudgetConstraints(host_prompt_tokens=100000)):
                with self.assertRaisesRegex(ValueError, "final input"):
                    _ = [e async for e in guarded.stream_for("main", "x" * 1000, (), (), constraints=override)]
            generous, ledger = self.guarded(model)
            with self.assertRaisesRegex(ValueError, "final input"):
                _ = [e async for e in generous.stream_for("semantic-summary", "x" * 1000, (), (),
                    constraints=RequestBudgetConstraints(auxiliary_input_tokens=500))]
            # Source-only allowance permits this input, but actual output512 exhausts total900.
            with self.assertRaisesRegex(ValueError, "final input"):
                _ = [e async for e in generous.stream_for("semantic-summary", "x" * 100, (), (),
                    constraints=RequestBudgetConstraints(auxiliary_input_tokens=1000, auxiliary_total_tokens=900))]
            _ = [e async for e in generous.stream("system", (Message("user", "ok"),), ())]
            prepared = await model.prepare_request("system", (Message("user", "ok"),), ())
            self.assertEqual(ledger.reserved[0][2], PromptTokenCounter().prepared(prepared) + 512 + 64)
            self.assertEqual(len(seen), 1)

    async def test_text_attachment_actual_blob_counted_image_without_policy_rejected(self):
        raw = "附件真实文本".encode()
        ref = AttachmentRef(hashlib.sha256(raw).hexdigest(), "text/plain", len(raw), "note.txt")
        class Resolver:
            def read(self, reference):
                return raw
        seen = []
        def handle(request):
            seen.append(json.loads(request.content))
            return response(ApiProtocol.CHAT_COMPLETIONS)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            model = OpenAIChatClient(self.config(ApiProtocol.CHAT_COMPLETIONS), http_client=http,
                max_output_tokens=128, attachment_resolver=Resolver(), input_modalities=(InputModality.TEXT, InputModality.IMAGE))
            guarded, ledger = self.guarded(model)
            _ = [e async for e in guarded.stream("system", (Message("user", "read", attachments=(ref,)),), ())]
            self.assertIn("附件真实文本", json.dumps(seen[0], ensure_ascii=False))
            image = AttachmentRef(hashlib.sha256(raw).hexdigest(), "image/png", len(raw), "image.png", 1, 1)
            with self.assertRaisesRegex(ValueError, "calibrated token policy"):
                _ = [e async for e in guarded.stream("system", (Message("user", "view", attachments=(image,)),), ())]
            self.assertEqual((len(seen), len(ledger.reserved)), (1, 1))

    async def test_auth_envelope_and_codex_output_diagnostics_after_transform(self):
        for provider, api, factory, base in (("antigravity", ApiProtocol.GOOGLE_GENERATIVE_AI,
            GoogleGenerativeAIClient, "https://cloudcode-pa.googleapis.com"),
            ("openai-codex", ApiProtocol.CODEX_RESPONSES, OpenAIResponsesClient, "https://chatgpt.com/backend-api/codex")):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("prepare must not send HTTP"))) as http:
                model = factory(self.config(api, provider, base), http_client=http, max_output_tokens=128)
                with patch("code_agent.providers.transport.Credential", return_value=Credential("oauth", "private-oauth",
                    extra={"projectId": "explicit-project", "accountId": "account"})):
                    prepared = await model.prepare_request("system", (Message("user", "hello"),), ())
                body = json.loads(prepared.body)
                self.assertNotIn("private-oauth", repr(prepared) + prepared.input_text)
                if provider == "antigravity":
                    self.assertEqual(body["project"], "explicit-project")
                    self.assertIn("request", body)
                else:
                    self.assertNotIn("max_output_tokens", body)
                    self.assertFalse(prepared.output_limit_enforced)
                    self.assertTrue(prepared.diagnostics)

    async def test_retry_reuses_exact_prepared_bytes_and_single_admission(self):
        seen = []
        def handle(request):
            seen.append(request.content)
            return httpx.Response(503, content=b"retry") if len(seen) == 1 else response(ApiProtocol.CHAT_COMPLETIONS)
        async def no_wait(_):
            pass
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            model = OpenAIChatClient(self.config(ApiProtocol.CHAT_COMPLETIONS, retries=1), http_client=http,
                sleep=no_wait, max_output_tokens=128)
            guarded, ledger = self.guarded(model)
            _ = [e async for e in guarded.stream("system", (Message("user", "hello"),), ())]
            self.assertEqual(len(seen), 2)
            self.assertEqual(seen[0], seen[1])
            self.assertEqual((len(ledger.reserved), len(ledger.settled)), (1, 1))

    async def test_missing_usage_keeps_admission_and_partial_usage_keeps_unknown_liability(self):
        for content, expected_settlements in (
            (b'data: {"type":"response.completed","response":{}}\n\n', 0),
            (b'data: {"type":"response.completed","response":{"usage":{"input_tokens":3,"output_tokens":1}}}\n\n', 1),
        ):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))) as http:
                model = OpenAIResponsesClient(self.config(ApiProtocol.RESPONSES), http_client=http, max_output_tokens=128)
                guarded, ledger = self.guarded(model)
                if expected_settlements:
                    _ = [e async for e in guarded.stream("system", (), ())]
                    self.assertTrue(ledger.settled[0][1]["completed"])
                else:
                    with self.assertRaisesRegex(RuntimeError, "omitted token usage"):
                        _ = [e async for e in guarded.stream("system", (), ())]
                self.assertEqual((len(ledger.reserved), len(ledger.settled)), (1, expected_settlements))

    async def test_early_close_drains_response_and_settles_partial_usage_as_incomplete(self):
        class BlockingStream(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                yield b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":1}}\n\n'
                await asyncio.Event().wait()
            async def aclose(self):
                self.closed = True
        raw = BlockingStream()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=raw))) as http:
            model = OpenAIChatClient(self.config(ApiProtocol.CHAT_COMPLETIONS), http_client=http, max_output_tokens=128)
            guarded, ledger = self.guarded(model)
            stream = guarded.stream("system", (), ())
            await anext(stream)
            await stream.aclose()
            self.assertTrue(raw.closed)
            self.assertEqual((len(ledger.reserved), len(ledger.settled)), (1, 1))
            self.assertFalse(ledger.settled[0][1]["completed"])
