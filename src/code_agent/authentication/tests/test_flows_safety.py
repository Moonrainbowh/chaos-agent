from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from code_agent.authentication.callback import BrowserCallback, parse_code
from code_agent.authentication.device import poll
from code_agent.authentication.flows_common import safe_url
from code_agent.authentication.models import AuthError, Credential
from code_agent.authentication.oauth import login, refresh


class SafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "ANTIGRAVITY_OAUTH_CLIENT_ID": "test-antigravity-client",
            "ANTIGRAVITY_OAUTH_CLIENT_SECRET": "test-antigravity-secret",
        })
        environment.start()
        self.addCleanup(environment.stop)

    async def test_refresh_retains_or_rotates_token(self):
        for provider in ("anthropic", "kimi-coding", "xai", "radius"):
            for value, expected in (({}, "old-refresh"), ({"refresh_token": "rotated"}, "rotated")):
                with self.subTest(provider=provider, expected=expected):
                    def handler(request):
                        self.assertIn(b"old-refresh", request.content)
                        return httpx.Response(200, json={"access_token": "fresh", **value})
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        result = await refresh(provider, Credential("oauth", "old", "old-refresh"), client=client)
                    self.assertEqual(result.refresh, expected)

    async def test_nonrenewable_policies(self):
        value = Credential("oauth", "key")
        self.assertIs(await refresh("openrouter", value), value)
        with self.assertRaisesRegex(AuthError, "cannot refresh"):
            await refresh("muse-code", value)
        with self.assertRaisesRegex(AuthError, "refresh token missing"):
            await refresh("anthropic", value)

    async def test_server_errors_do_not_expose_credentials_or_body(self):
        secret = "server-secret-access-refresh"
        for status, body in ((401, {"error": secret}), (500, {"message": secret})):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body))) as client:
                with self.assertRaises(AuthError) as caught:
                    await refresh("anthropic", Credential("oauth", secret, secret), client=client)
                self.assertNotIn(secret, str(caught.exception))

    async def test_network_errors_do_not_echo_request_url(self):
        def handler(request):
            raise httpx.ConnectError("secret URL and token", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(AuthError, "network request failed") as caught:
                await refresh("anthropic", Credential("oauth", "a", "r"), client=client)
            self.assertNotIn("secret", str(caught.exception))

    async def test_timeout_cancels_inflight_request(self):
        cancelled = asyncio.Event()
        async def handler(request):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(AuthError, "timed out"):
                await login("kimi-coding", client=client, timeout=0.01)
        self.assertTrue(cancelled.is_set())

    async def test_cancel_before_network(self):
        cancel = asyncio.Event()
        cancel.set()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("network called"))) as client:
            with self.assertRaisesRegex(AuthError, "cancelled"):
                await login("kimi-coding", client=client, cancel=cancel)

    async def test_poll_slow_down_changes_next_wait(self):
        replies = [(400, {"error": "slow_down"}), (200, {"access_token": "a"})]
        def handler(_):
            status, value = replies.pop(0)
            return httpx.Response(status, json=value)
        with patch("code_agent.authentication.device.asyncio.sleep", new=AsyncMock()) as sleep:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result = await poll(client, "https://example.com/token", {}, setup={"interval": 2})
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 7])
        self.assertEqual(result["access_token"], "a")

    async def test_poll_denied_expired_and_unknown_errors(self):
        for error, message in (("access_denied", "denied"), ("expired_token", "expired"), ("secret", "failed")):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(400, json={"error": error}))) as client:
                with self.assertRaisesRegex(AuthError, message):
                    await poll(client, "https://example.com/token", {}, immediate=True)

    async def test_callback_state_validation_and_listener_cleanup(self):
        async with BrowserCallback("http://127.0.0.1:0/callback", "state") as callback:
            address = callback.server.sockets[0].getsockname()
            for query, status in (("code=bad&state=wrong", b"400"), ("code=good&state=state", b"200")):
                reader, writer = await asyncio.open_connection(*address)
                writer.write(f"GET /callback?{query} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
                await writer.drain()
                self.assertIn(status, await reader.readline())
                writer.close()
                await writer.wait_closed()
            self.assertEqual(await callback.result, "good")
        with self.assertRaises(OSError):
            await asyncio.open_connection(*address)

    async def test_manual_callback_fallback(self):
        with patch("code_agent.authentication.callback.asyncio.start_server", side_effect=OSError), \
             patch("code_agent.authentication.callback.show", new=AsyncMock()):
            async with BrowserCallback("http://127.0.0.1:1234/callback", "state") as callback:
                result = await callback.receive("https://example.com/auth", lambda _: None,
                                                AsyncMock(return_value="http://localhost/callback?code=c&state=state"))
        self.assertEqual(result, "c")

    def test_untrusted_urls_and_pasted_state_rejected(self):
        for value in ("javascript:alert(1)", "https://user:pass@example.com", "http://remote.example/auth"):
            with self.assertRaises(AuthError):
                safe_url(value)
        with self.assertRaisesRegex(AuthError, "state mismatch"):
            parse_code("http://localhost/callback?code=c&state=wrong", "expected", pasted=True)

    async def test_antigravity_identity_change_rejected_without_network(self):
        value = Credential("oauth", "a", "r", extra={"oauthClientId": "different"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("network called"))) as client:
            with self.assertRaisesRegex(AuthError, "client changed"):
                await refresh("antigravity", value, client=client)

    async def test_workbuddy_refresh_preserves_account_and_rotates(self):
        replies = [{"data": {"accessToken": "new", "refreshToken": "rotated"}},
                   {"data": {"accounts": [{"uid": "u", "enterpriseId": "e", "departmentFullName": "new"}]}}]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=replies.pop(0)))) as client:
            result = await refresh("workbuddy", Credential("oauth", "a", "r", extra={
                "workbuddyAccount": {"uid": "u", "enterpriseId": "e"},
                "workbuddyEndpoint": "https://copilot.tencent.com",
            }), client=client)
        self.assertEqual(result.refresh, "rotated")
        self.assertEqual(result.extra["workbuddyAccount"]["departmentFullName"], "new")
