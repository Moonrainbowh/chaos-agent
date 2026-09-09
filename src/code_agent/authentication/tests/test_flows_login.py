from __future__ import annotations

import base64
import json
import os
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx

from code_agent.authentication.oauth import login


def jwt():
    payload = {"https://api.openai.com/auth": {"chatgpt_account_id": "account-test"}}
    return "head." + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=") + ".signature"


class FakeCallback:
    seen = []

    def __init__(self, redirect, state):
        self.redirect = redirect.replace(":0/", ":12345/")
        self.state = state
        self.server = object()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def receive(self, url, display, read_input):
        self.seen.append(url)
        return "test-code"


class LoginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "ANTIGRAVITY_OAUTH_CLIENT_ID": "test-antigravity-client",
            "ANTIGRAVITY_OAUTH_CLIENT_SECRET": "test-antigravity-secret",
        })
        environment.start()
        self.addCleanup(environment.stop)

    async def asyncSetUp(self):
        FakeCallback.seen = []
        self.patches = [patch("code_agent.authentication.flows_browser.BrowserCallback", FakeCallback),
                        patch("code_agent.authentication.flows_antigravity.BrowserCallback", FakeCallback),
                        patch("code_agent.authentication.flows_common.webbrowser.open", return_value=True),
                        patch("code_agent.authentication.device.asyncio.sleep", new=AsyncMock())]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()

    async def run_flow(self, provider, replies, **kwargs):
        requests = []

        def handler(request):
            requests.append(request)
            status, value = replies.pop(0)
            return httpx.Response(status, json=value)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await login(provider, client=client, display=lambda _: None, **kwargs)
        self.assertFalse(replies)
        return result, requests

    async def test_anthropic_browser_pkce_json_exchange(self):
        result, requests = await self.run_flow("anthropic", [(200, {"access_token": "a", "refresh_token": "r"})])
        fields = parse_qs(urlsplit(FakeCallback.seen[0]).query)
        body = json.loads(requests[0].content)
        self.assertEqual(body["state"], fields["state"][0])
        self.assertEqual(body["code_verifier"], fields["state"][0])
        self.assertEqual(body["code"], "test-code")
        self.assertEqual(result.refresh, "r")

    async def test_codex_browser_extracts_routing_identity(self):
        result, requests = await self.run_flow("openai-codex", [(200, {"access_token": jwt(), "refresh_token": "r"})])
        self.assertEqual(result.extra["accountId"], "account-test")
        self.assertIn(b"code_verifier=", requests[0].content)
        self.assertEqual(requests[0].url.path, "/oauth/token")

    async def test_codex_device_pending_then_code_exchange(self):
        result, requests = await self.run_flow("openai-codex", [
            (200, {"device_auth_id": "id", "user_code": "123", "interval": 1}),
            (403, {}), (200, {"authorization_code": "code", "code_verifier": "verifier"}),
            (200, {"access_token": jwt(), "refresh_token": "r"}),
        ], method="device_code")
        self.assertEqual(result.extra["accountId"], "account-test")
        self.assertIn(b"https%3A%2F%2Fauth.openai.com%2Fdeviceauth%2Fcallback", requests[-1].content)

    async def test_radius_browser_discovery_and_gateway_retention(self):
        result, requests = await self.run_flow("radius", [
            (200, {"authorizationEndpoint": "https://identity.example/authorize"}),
            (200, {"access_token": "a", "refresh_token": "r"}),
        ], options={"gateway": "https://radius.example/"})
        self.assertEqual(result.extra["gateway"], "https://radius.example")
        self.assertEqual(requests[0].url.path, "/v1/oauth")
        self.assertTrue(FakeCallback.seen[0].startswith("https://identity.example/authorize?"))

    async def test_openrouter_mints_nonexpiring_key(self):
        result, requests = await self.run_flow("openrouter", [(200, {"key": "minted"})])
        self.assertEqual(result.access, "minted")
        self.assertIsNone(result.expires_at)
        self.assertIn("callback_url", parse_qs(urlsplit(FakeCallback.seen[0]).query))
        self.assertEqual(requests[0].url.path, "/api/v1/auth/keys")

    async def test_standard_device_platforms(self):
        for provider, device_path, token_path in [
            ("kimi-coding", "/api/oauth/device_authorization", "/api/oauth/token"),
            ("xai", "/oauth2/device/code", "/oauth2/token"),
            ("radius", "/v1/oauth/device", "/v1/oauth/token"),
        ]:
            with self.subTest(provider=provider):
                result, requests = await self.run_flow(provider, [
                    (200, {"device_code": "device", "user_code": "123", "verification_uri": "https://example.com/device"}),
                    (400, {"error": "authorization_pending"}),
                    (200, {"access_token": "access", "refresh_token": "refresh"}),
                ], **({"method": "device_code"} if provider == "radius" else {}))
                self.assertEqual(result.access, "access")
                self.assertEqual(requests[0].url.path, device_path)
                self.assertEqual(requests[-1].url.path, token_path)

    async def test_copilot_exchanges_github_token(self):
        result, requests = await self.run_flow("github-copilot", [
            (200, {"device_code": "device", "user_code": "123", "verification_uri": "https://github.com/login/device"}),
            (200, {"access_token": "github-secret"}),
            (200, {"token": "copilot-secret", "expires_at": 1900000000}),
        ])
        self.assertEqual(result.access, "copilot-secret")
        self.assertEqual(result.refresh, "github-secret")
        self.assertEqual(requests[-1].headers["Authorization"], "Bearer github-secret")

    async def test_muse_mints_model_key_after_meta_device(self):
        result, requests = await self.run_flow("muse-code", [
            (200, {"device_code": "d", "user_code": "u", "verification_uri": "https://auth.meta.com/device"}),
            (200, {"access_token": "meta-account"}),
            (200, {"api_key": "model-key", "user_id": "uid", "is_subs_active": True}),
        ])
        self.assertEqual(result.access, "model-key")
        self.assertEqual(result.extra["oauthAccessToken"], "meta-account")
        self.assertEqual(requests[0].url.path, "/oidc/device/authorization/")
        self.assertEqual(json.loads(requests[-1].content), {"onboard": True})

    async def test_antigravity_requires_project_onboarding(self):
        result, requests = await self.run_flow("antigravity", [
            (200, {"access_token": "google", "refresh_token": "r"}),
            (200, {"email": "test@example.com"}),
            (200, {"allowedTiers": [{"id": "free", "isDefault": True}]}),
            (200, {"done": True, "response": {"cloudaicompanionProject": {"id": "project"}}}),
        ])
        self.assertEqual(result.extra["projectId"], "project")
        self.assertEqual(requests[-1].url.path, "/v1internal:onboardUser")
        self.assertIn("oauthClientId", result.extra)

    async def test_workbuddy_polls_then_merges_account(self):
        result, requests = await self.run_flow("workbuddy", [
            (200, {"code": 0, "data": {"state": "state", "authUrl": "https://copilot.tencent.com/login"}}),
            (200, {"code": 11217}),
            (200, {"data": {"accessToken": "a", "refreshToken": "r", "method": "wechat"}}),
            (200, {"code": 12151}),
            (200, {"data": {"uid": "u", "enterpriseId": "e"}}),
            (200, {"data": {"accounts": [{"uid": "u", "enterpriseId": "e", "departmentFullName": "D"}]}}),
        ])
        self.assertEqual(result.extra["workbuddyAccount"]["departmentFullName"], "D")
        self.assertEqual(result.extra["workbuddyAuthMethod"], "wechat")
        self.assertNotIn("accessToken", result.extra["workbuddyAuth"])
        self.assertEqual(requests[0].headers["X-Product"], "SaaS")
