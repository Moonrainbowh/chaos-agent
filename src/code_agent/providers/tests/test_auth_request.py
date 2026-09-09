from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from code_agent.authentication.models import Credential
from code_agent.providers.auth_request import authenticated_request
from code_agent.providers.config import ApiProtocol, ProviderConfig, ConfiguredApiKey
from code_agent.providers.errors import ProviderConfigError
from code_agent.providers.openai_responses import OpenAIResponsesClient
from code_agent.providers.google_generative_ai import GoogleGenerativeAIClient
from code_agent.providers.anthropic import AnthropicClient


class AuthRequestTests(unittest.IsolatedAsyncioTestCase):
    def test_workbuddy_cloud_chat_uses_bound_route_and_max_tokens(self):
        config = SimpleNamespace(provider_id="workbuddy", api=ApiProtocol.CHAT_COMPLETIONS,
                                 base_url="https://ignored.invalid", model="auto")
        credential = Credential("oauth", "secret", extra={"workbuddyEndpoint": "https://copilot.tencent.com"})
        original = {"model": "auto", "max_completion_tokens": 16000}
        url, body, _ = authenticated_request(config, credential, "/ignored", original, {})
        self.assertEqual(url, "https://copilot.tencent.com/v2/chat/completions")
        self.assertEqual(body["max_tokens"], 16000)
        self.assertNotIn("max_completion_tokens", body)
        self.assertIn("max_completion_tokens", original)

    async def wire(self, provider, api, base, credential, content):
        config = ProviderConfig(base_url=base, model="test-model", api=api,
            provider_id=provider, api_key_source=ConfiguredApiKey("unused"))
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, content=content)
        factory = {ApiProtocol.CODEX_RESPONSES: OpenAIResponsesClient,
                   ApiProtocol.GOOGLE_GENERATIVE_AI: GoogleGenerativeAIClient,
                   ApiProtocol.ANTHROPIC_MESSAGES: AnthropicClient}[api]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = factory(config, http_client=http)
            with patch("code_agent.providers.transport.Credential", return_value=credential):
                events = [event async for event in client.stream("system", [], [])]
        return seen[0], events

    async def test_codex_actual_payload_and_account_header(self):
        req, events = await self.wire("openai-codex", ApiProtocol.CODEX_RESPONSES,
            "https://chatgpt.com/backend-api/codex", Credential("oauth", "token", extra={"accountId": "account"}),
            b'data: {"type":"response.completed","response":{}}\n\n')
        body = json.loads(req.content)
        self.assertFalse(body["store"])
        self.assertNotIn("max_output_tokens", body)
        self.assertEqual(body["include"], ["reasoning.encrypted_content"])
        self.assertEqual(req.headers["chatgpt-account-id"], "account")

    async def test_antigravity_wrapped_request_and_response(self):
        req, events = await self.wire("antigravity", ApiProtocol.GOOGLE_GENERATIVE_AI,
            "https://cloudcode-pa.googleapis.com", Credential("oauth", "token", extra={"projectId": "p1"}),
            b'data: {"response":{"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}}\n\n')
        self.assertEqual(req.url.path, "/v1internal:streamGenerateContent")
        self.assertNotIn("x-goog-api-key", req.headers)
        self.assertEqual(req.headers["authorization"], "Bearer token")
        self.assertEqual(json.loads(req.content)["project"], "p1")
        self.assertEqual(events[0].text, "ok")

    async def test_anthropic_oauth_replaces_api_key(self):
        req, _ = await self.wire("anthropic", ApiProtocol.ANTHROPIC_MESSAGES,
            "https://api.anthropic.com", Credential("oauth", "token"),
            b'data: {"type":"message_stop"}\n\n')
        self.assertNotIn("x-api-key", req.headers)
        self.assertEqual(req.headers["authorization"], "Bearer token")
        self.assertIn("oauth-2025-04-20", req.headers["anthropic-beta"])

    def test_oauth_endpoint_redirect_rejected(self):
        config = SimpleNamespace(provider_id="openai-codex", base_url="https://attacker.test", api=ApiProtocol.CODEX_RESPONSES)
        with self.assertRaises(ProviderConfigError):
            authenticated_request(config, Credential("oauth", "token"), "/responses", {}, {})

    def test_workbuddy_bound_endpoint_and_identity(self):
        config = SimpleNamespace(provider_id="workbuddy", base_url="https://ignored.test", api=ApiProtocol.ANTHROPIC_MESSAGES)
        credential = Credential("oauth", "token", extra={"workbuddyEndpoint": "https://company.test/base",
            "workbuddyAccount": {"uid": "u1", "enterpriseId": "e1"}})
        url, _, headers = authenticated_request(config, credential, "/messages", {}, {})
        self.assertEqual(url, "https://company.test/base/v2/v1/messages")
        self.assertEqual(headers["x-user-id"], "u1")
        self.assertEqual(headers["x-tenant-id"], "e1")
        self.assertNotIn("x-api-key", headers)

    def test_cloudflare_gateway_uses_credential_coordinates(self):
        config = SimpleNamespace(provider_id="cloudflare-ai-gateway", base_url="https://ignored.test", api=ApiProtocol.CHAT_COMPLETIONS)
        credential = Credential("api_key", "token", extra={"accountId": "a", "gatewayId": "g"})
        url, _, _ = authenticated_request(config, credential, "/chat/completions", {}, {})
        self.assertEqual(url, "https://gateway.ai.cloudflare.com/v1/a/g/compat/chat/completions")

    def test_all_oauth_platform_routes_are_bound(self):
        rows = [
            ("anthropic", ApiProtocol.ANTHROPIC_MESSAGES, "https://api.anthropic.com", {}, "/v1/messages"),
            ("openai-codex", ApiProtocol.CODEX_RESPONSES, "https://chatgpt.com/backend-api", {}, "/backend-api/codex/responses"),
            ("github-copilot", ApiProtocol.CHAT_COMPLETIONS, "https://api.githubcopilot.com", {}, "/chat/completions"),
            ("kimi-coding", ApiProtocol.ANTHROPIC_MESSAGES, "https://api.kimi.com/coding", {}, "/coding/v1/messages"),
            ("openrouter", ApiProtocol.CHAT_COMPLETIONS, "https://openrouter.ai/api/v1", {}, "/api/v1/chat/completions"),
            ("xai", ApiProtocol.RESPONSES, "https://api.x.ai/v1", {}, "/v1/responses"),
            ("muse-code", ApiProtocol.RESPONSES, "https://api.meta.ai/v1", {}, "/v1/responses"),
            ("radius", ApiProtocol.PI_MESSAGES, "https://radius.pi.dev/v1", {"gateway": "https://radius.pi.dev"}, "/v1/messages"),
            ("workbuddy", ApiProtocol.ANTHROPIC_MESSAGES, "https://copilot.tencent.com/v2", {"workbuddyEndpoint": "https://copilot.tencent.com/v2"}, "/v2/v1/messages"),
            ("antigravity", ApiProtocol.GOOGLE_GENERATIVE_AI, "https://cloudcode-pa.googleapis.com", {"projectId": "p"}, "/v1internal:streamGenerateContent?alt=sse"),
        ]
        for provider, api, base, extra, expected in rows:
            with self.subTest(provider=provider):
                config = SimpleNamespace(provider_id=provider, api=api, base_url=base, model="m")
                url, _, headers = authenticated_request(config, Credential("oauth", "secret", extra=extra),
                                                        "/attacker/path", {}, {"x-api-key": "secret"})
                self.assertTrue(url.endswith(expected), url)
                self.assertEqual(headers["authorization"], "Bearer secret")
                self.assertNotIn("x-api-key", headers)

    def test_api_key_anthropic_retains_native_auth_without_oauth_identity(self):
        config = SimpleNamespace(provider_id="anthropic", api=ApiProtocol.ANTHROPIC_MESSAGES,
                                 base_url="https://api.anthropic.com")
        _, _, headers = authenticated_request(config, Credential("api_key", "key"),
                                               "/v1/messages", {}, {"x-api-key": "key"})
        self.assertEqual(headers["x-api-key"], "key")
        self.assertNotIn("authorization", headers)
        self.assertNotIn("anthropic-beta", headers)

    def test_oauth_untrusted_ports_and_userinfo_rejected(self):
        for base in ("https://chatgpt.com:444", "https://user@chatgpt.com", "https://chatgpt.com?bad=1"):
            config = SimpleNamespace(provider_id="openai-codex", api=ApiProtocol.CODEX_RESPONSES, base_url=base)
            with self.subTest(base=base), self.assertRaises(ProviderConfigError):
                authenticated_request(config, Credential("oauth", "key"), "/responses", {}, {})

    def test_oauth_only_platforms_reject_api_keys(self):
        for provider in ("openai-codex", "antigravity"):
            with self.subTest(provider=provider), self.assertRaises(ProviderConfigError):
                authenticated_request(SimpleNamespace(provider_id=provider), Credential("api_key", "key"), "", {}, {})

    def test_radius_preserves_catalog_base_path_and_rejects_other_origin(self):
        credential = Credential("oauth", "token", extra={"gateway": "https://radius.pi.dev"})
        config = SimpleNamespace(provider_id="radius", api=ApiProtocol.PI_MESSAGES, base_url="https://radius.pi.dev/v1")
        url, _, _ = authenticated_request(config, credential, "/messages", {}, {})
        self.assertEqual(url, "https://radius.pi.dev/v1/messages")
        for base in ("https://other.test/v1", "https://radius.pi.dev:8443/v1"):
            config.base_url = base
            with self.subTest(base=base), self.assertRaises(ProviderConfigError):
                authenticated_request(config, credential, "/messages", {}, {})


if __name__ == "__main__":
    unittest.main()
