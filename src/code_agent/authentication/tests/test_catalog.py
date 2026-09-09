from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from code_agent.authentication.catalog import ModelCatalog, parse_provider_payload
from code_agent.authentication.registry import OAUTH_METHODS, get_provider, list_providers


class RegistryTests(unittest.TestCase):
    def test_oauth_parity_and_key_boundaries(self):
        self.assertEqual(len(OAUTH_METHODS), 10)
        self.assertEqual(len(list_providers()), 40)
        for spec in list_providers():
            self.assertEqual(spec.offers_api_key, spec.id not in {"antigravity", "openai-codex"})
        self.assertEqual(get_provider("openai-codex").login_methods, ("browser", "device_code"))
        self.assertTrue(get_provider("antigravity").experimental)
        self.assertEqual(get_provider("kimi-coding").request_path, "/v1/messages")

    def test_offline_catalog_does_not_network(self):
        with patch("httpx.AsyncClient", side_effect=AssertionError("unexpected network")):
            models = ModelCatalog().models()
        self.assertGreater(len(models), 1000)
        self.assertFalse(any(m.provider == "amazon-bedrock" for m in models))
        self.assertEqual(ModelCatalog().models("openai-codex")[0].request_path, "/codex/responses")

    def test_filters_unsupported_hidden_and_credential_urls(self):
        base = {"id": "m", "api": "openai-responses", "baseUrl": "https://api.example/v1"}
        bad = [dict(base, api="bedrock-converse-stream"), dict(base, hidden=True),
               dict(base, baseUrl="https://user:secret@api.example"), dict(base, api=[]),
               dict(base, baseUrl="$(some-command)"), dict(base, baseUrl="https://[bad")]
        self.assertEqual(parse_provider_payload("p", bad), ())
        model = parse_provider_payload("p", {"models": [dict(base, headers={"Authorization": "secret"})]})[0]
        self.assertEqual(model.provider, "p")
        self.assertFalse(hasattr(model, "headers"))
        self.assertEqual(parse_provider_payload("p", [dict(base, input=["audio"])]), ())
        mixed = parse_provider_payload("p", [dict(base, input=["text", "audio", {}])])[0]
        self.assertEqual(mixed.input_modalities, ("text",))
        with self.assertRaises(ValueError):
            parse_provider_payload("p", {"models": "invalid"})


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_partial_failure_cache_and_protocol_metadata(self):
        def handler(request):
            path = request.url.path
            if path.endswith("/providers"):
                return httpx.Response(200, json=["openai", "anthropic", "bedrock"])
            if path.endswith("/anthropic"):
                return httpx.Response(503)
            if path.endswith("/bedrock"):
                return httpx.Response(200, json=[{"id": "b", "api": "bedrock-converse-stream"}])
            return httpx.Response(200, json={"new-model": {
                "id": "new-model", "name": "New", "api": "openai-responses",
                "baseUrl": "https://api.openai.com/v1", "contextWindow": 20000,
                "maxTokens": 1500, "input": ["text", "image"],
                "headers": {"Authorization": "not-copied"},
            }})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "models.json"
            catalog = ModelCatalog(path)
            original = catalog.models("anthropic")
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                report = await catalog.refresh(client)
            self.assertEqual(report.failed_providers, ("anthropic",))
            self.assertEqual(catalog.models("anthropic"), original)
            self.assertEqual(catalog.models("bedrock"), ())
            model = ModelCatalog(path).models("openai")[0]
            self.assertEqual(model.id, "new-model")
            self.assertEqual(model.max_output_tokens, 1500)
            self.assertEqual(model.input_modalities, ("text", "image"))
            self.assertNotIn("Authorization", path.read_text())

    async def test_invalid_provider_directory_makes_no_followup_requests(self):
        calls = []
        def handler(request):
            calls.append(request.url)
            return httpx.Response(200, json=["../../secret"])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ValueError):
                await ModelCatalog().refresh(client)
        self.assertEqual(len(calls), 1)
