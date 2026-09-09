from __future__ import annotations

import unittest

import httpx

from code_agent.authentication.flows_browser import gateway
from code_agent.authentication.models import AuthError, Credential
from code_agent.authentication.oauth import refresh


class RadiusOriginTests(unittest.IsolatedAsyncioTestCase):
    def test_gateway_normalizes_to_origin(self):
        for supplied, expected in (
            ("radius.example/custom/path", "https://radius.example"),
            ("https://radius.example:8443/v1/", "https://radius.example:8443"),
            ("http://127.0.0.1:5555/path", "http://127.0.0.1:5555"),
            ("http://[::1]:5555/path", "http://[::1]:5555"),
            ("", "https://radius.pi.dev"),
        ):
            with self.subTest(supplied=supplied):
                self.assertEqual(gateway({"gateway": supplied}), expected)

    def test_gateway_rejects_query_and_userinfo(self):
        for supplied in ("https://radius.example?query=secret", "https://radius.example?",
                         "https://user:secret@radius.example", "https://@radius.example",
                         "https://radius.example/#", "http://remote.example"):
            with self.subTest(supplied=supplied), self.assertRaises(AuthError):
                gateway({"gateway": supplied})

    async def test_refresh_preserves_custom_issuer_and_canonicalizes_metadata(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"access_token": "fresh"})
        previous = Credential("oauth", "old", "refresh", extra={"gateway": "https://radius.example:8443/path"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await refresh("radius", previous, client=client)
        self.assertEqual(str(seen[0].url), "https://radius.example:8443/v1/oauth/token")
        self.assertEqual(result.extra["gateway"], "https://radius.example:8443")
        self.assertEqual(result.refresh, "refresh")
