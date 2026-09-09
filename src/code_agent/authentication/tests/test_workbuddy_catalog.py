import base64
import json
import unittest

import httpx

from code_agent.authentication.models import AuthError, Credential
from code_agent.authentication.workbuddy_catalog import discover


def credential(kind="oauth"):
    return Credential(kind, "private-token", extra={
        "workbuddyEndpoint": "https://copilot.tencent.com/v2",
        "workbuddyDomain": "account-domain", "workbuddyAuthMethod": "wechat",
        "workbuddyAccount": {"uid": "user-1", "enterpriseId": "enterprise-2",
                             "departmentFullName": "dept", "idSource": "source"}})


def model(**extra):
    return {"id": "cloud-chat", "name": "Cloud Chat", "maxInputTokens": 128000,
            "maxOutputTokens": 8192, "supportsToolCall": True, "supportsImages": True, **extra}


class WorkBuddyCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_cloud_config_uses_bound_account_headers_and_chat_protocol(self):
        def handler(request):
            self.assertEqual(str(request.url), "https://copilot.tencent.com/v3/config")
            self.assertEqual(request.headers["authorization"], "Bearer private-token")
            self.assertEqual(request.headers["x-user-id"], "user-1")
            self.assertEqual(request.headers["x-enterprise-id"], "enterprise-2")
            self.assertEqual(request.headers["x-tenant-id"], "enterprise-2")
            self.assertEqual(request.headers["x-domain"], "account-domain")
            self.assertEqual(request.headers["x-auth-method"], "wechat")
            self.assertFalse(any(k.startswith("x-no-") for k in request.headers))
            self.assertEqual(json.loads(base64.b64decode(request.headers["x-userinfo"])), {
                "uin": "user-1", "owner_uin": "enterprise-2", "id_source": "source", "token_source": "wechat"})
            self.assertEqual(request.extensions["timeout"]["read"], 5)
            return httpx.Response(200, json={"code": "0", "data": {"data": {"models": [
                model(), model(id="no-tools", supportsToolCall=False), model(id="invalid", maxInputTokens=True),
                model(id="text", supportsImages=False)]}}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            models = await discover(credential(), client)
        self.assertEqual([m.id for m in models], ["cloud-chat", "text"])
        self.assertEqual(models[0].protocol, "chat_completions")
        self.assertEqual(models[0].base_url, "https://copilot.tencent.com/v2")
        self.assertEqual(models[0].request_path, "/chat/completions")
        self.assertEqual(models[0].context_window, 128000)
        self.assertEqual(models[0].max_output_tokens, 8192)
        self.assertEqual(models[1].input_modalities, ("text",))

    async def test_api_key_and_redirect_are_not_forwarded(self):
        seen = []
        def handler(request):
            seen.append(request.url)
            self.assertEqual(request.headers["x-api-key"], "private-token")
            return httpx.Response(302, headers={"location": "https://other.example/private-token"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            with self.assertRaises(AuthError) as caught:
                await discover(credential("api_key"), client)
        self.assertEqual(len(seen), 1)
        self.assertNotIn("private-token", str(caught.exception))

    async def test_bad_cloud_reply_has_no_secret_in_error(self):
        for payload in ({"code": 7, "message": "private-token"}, {"data": {}},
                        {"data": {"models": []}}, {"models": [model(maxOutputTokens=0)]}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))) as client:
                with self.assertRaises(AuthError) as caught:
                    await discover(credential(), client)
            self.assertNotIn("private-token", str(caught.exception))

    async def test_invalid_endpoint_fails_before_request(self):
        bad = Credential("oauth", "private-token", extra={"workbuddyEndpoint": "http://other.example"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: self.fail("unexpected request"))) as client:
            with self.assertRaises(AuthError):
                await discover(bad, client)
