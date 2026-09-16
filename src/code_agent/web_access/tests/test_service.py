import unittest

import httpx

from code_agent.web_access.service import WebAccessService


class TestWebAccessService(unittest.IsolatedAsyncioTestCase):
    async def test_search_is_bounded_and_returns_candidates(self):
        async def handler(request):
            return httpx.Response(200, text='<a class="result__a" href="https://example.test/a">A</a>')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = await WebAccessService(client).search("q")
            self.assertEqual(results[0]["url"], "https://example.test/a")

    async def test_site_api_uses_dedicated_endpoint(self):
        seen = []
        async def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, text='{"full_name":"org/repo"}')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await WebAccessService(client).query_site("github", "org/repo")
        self.assertEqual(result.layer, "github_api")
        self.assertEqual(seen, ["https://api.github.com/repos/org/repo"])

    async def test_fetch_does_not_follow_redirects(self):
        async def handler(request):
            return httpx.Response(302, headers={"location": "https://example.test/next"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(RuntimeError, "redirect"):
                await WebAccessService(client).fetch("https://example.test")

    async def test_retrieve_routes_site_api_and_rejects_ambiguous_input(self):
        async def handler(request):
            return httpx.Response(200, text='{}')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = WebAccessService(client)
            result = await service.retrieve(site="github", identifier="org/repo")
            self.assertEqual(result["layer"], "site_api")
            with self.assertRaisesRegex(ValueError, "exactly one"):
                await service.retrieve(query="q", url="https://example.test")

