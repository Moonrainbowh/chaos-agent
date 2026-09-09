from __future__ import annotations

import base64
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from code_agent.authentication.models import AuthError, Credential
from code_agent.authentication.oauth import refresh


class RefreshTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "ANTIGRAVITY_OAUTH_CLIENT_ID": "test-antigravity-client",
            "ANTIGRAVITY_OAUTH_CLIENT_SECRET": "test-antigravity-secret",
        })
        environment.start()
        self.addCleanup(environment.stop)

    async def run_refresh(self, provider, credential, replies):
        requests = []
        def handler(request):
            requests.append(request)
            status, value = replies.pop(0)
            return httpx.Response(status, json=value)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            value = await refresh(provider, credential, client=client)
        self.assertFalse(replies)
        return value, requests

    async def test_kimi_transient_retry_retains_refresh(self):
        with patch("code_agent.authentication.flows_device.asyncio.sleep", new=AsyncMock()) as sleep:
            value, requests = await self.run_refresh("kimi-coding", Credential("oauth", "a", "r"), [
                (500, {"secret": "never surfaced"}), (429, {}), (200, {"access_token": "new"}),
            ])
        self.assertEqual(value.refresh, "r")
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertEqual(len(requests), 3)

    async def test_kimi_does_not_retry_invalid_grant(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(400, json={"error": "invalid_grant"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(AuthError):
                await refresh("kimi-coding", Credential("oauth", "a", "r"), client=client)
        self.assertEqual(len(seen), 1)

    async def test_antigravity_refresh_retains_project_if_enrichment_fails(self):
        previous = Credential("oauth", "a", "r", extra={"projectId": "existing"})
        value, _ = await self.run_refresh("antigravity", previous, [
            (200, {"access_token": "new"}), (500, {}), (401, {}),
        ])
        self.assertEqual(value.extra["projectId"], "existing")
        self.assertEqual(value.refresh, "r")

    async def test_antigravity_confirms_invalid_grant_once(self):
        with patch("code_agent.authentication.flows_antigravity.asyncio.sleep", new=AsyncMock()) as sleep:
            value, _ = await self.run_refresh("antigravity", Credential("oauth", "a", "r"), [
                (400, {"error": "invalid_grant"}), (200, {"access_token": "new", "refresh_token": "rotated"}),
                (200, {}), (200, {"cloudaicompanionProject": "project"}),
            ])
        self.assertEqual(value.refresh, "rotated")
        sleep.assert_awaited_once_with(0.5)

    async def test_codex_refresh_updates_account_routing(self):
        claims = {"https://api.openai.com/auth": {"chatgpt_account_id": "new-account"}}
        access = "h." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".s"
        value, _ = await self.run_refresh("openai-codex", Credential("oauth", "a", "r", extra={"accountId": "old"}), [
            (200, {"access_token": access}),
        ])
        self.assertEqual(value.extra["accountId"], "new-account")

    async def test_copilot_enterprise_refresh_routes_to_same_domain(self):
        value, requests = await self.run_refresh("github-copilot", Credential("oauth", "a", "github",
                                                    extra={"enterpriseUrl": "enterprise.example"}), [
            (200, {"token": "copilot", "expires_at": 1900000000}),
        ])
        self.assertEqual(requests[0].url.host, "api.enterprise.example")
        self.assertEqual(value.refresh, "github")

    async def test_redirect_never_forwards_credentials(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": "https://other.example/token"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            with self.assertRaises(AuthError):
                await refresh("anthropic", Credential("oauth", "a", "r"), client=client)
        self.assertEqual(len(requests), 1)
