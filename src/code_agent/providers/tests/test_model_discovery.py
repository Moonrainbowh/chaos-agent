import unittest
import httpx

from code_agent.providers.config import ProviderConfig, ConfiguredApiKey, ApiProtocol
from code_agent.providers.errors import ProviderError
from code_agent.providers.model_discovery import discover_models


def config(base="https://example.test", **values):
    return ProviderConfig(base, "seed", ApiProtocol.CHAT_COMPLETIONS,
                          api_key_source=ConfiguredApiKey("secret"), **values)


class ModelDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_paths_auth_and_deduplication(self):
        for base in ("https://example.test", "https://example.test/v1", "https://example.test/proxy"):
            seen = []
            def handler(request):
                seen.append(request)
                return httpx.Response(200, json={"data": [{"id": "z"}, {"id": "a"}, {"id": "z"}, {"id": "bad\n"}]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                self.assertEqual(await discover_models(config(base), client=client), ("a", "z"))
                self.assertFalse(client.is_closed)
            self.assertEqual(seen[0].headers["Authorization"], "Bearer secret")
            expected = base + ("/models" if base.endswith("/v1") else "/v1/models")
            self.assertEqual(str(seen[0].url), expected)

    async def test_error_bodies_and_redirects_are_not_exposed_or_followed(self):
        for status in (401, 302, 500):
            seen = []
            def handler(request):
                seen.append(request)
                return httpx.Response(status, text="secret", headers={"Location": "https://other.test"})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(ProviderError) as error:
                    await discover_models(config(), client=client)
            self.assertNotIn("secret", str(error.exception))
            self.assertEqual(len(seen), 1)

    async def test_empty_malformed_and_oversize_catalogs_fail(self):
        for body, limit in ((b'{}', 100), (b'no json', 100), (b'{"data": []}', 100), (b'x' * 101, 100)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))) as client:
                with self.assertRaises(ProviderError):
                    await discover_models(config(max_response_bytes=limit), client=client)
