import asyncio

import httpx
import pytest

from code_agent.web_access.service import WebAccessService


def test_search_is_bounded_and_returns_candidates():
    async def run():
        async def handler(request):
            return httpx.Response(200, text='<a class="result__a" href="https://example.test/a">A</a>')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert (await WebAccessService(client).search("q"))[0]["url"] == "https://example.test/a"
    asyncio.run(run())


def test_site_api_uses_dedicated_endpoint():
    async def run():
        seen = []
        async def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, text='{"full_name":"org/repo"}')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await WebAccessService(client).query_site("github", "org/repo")
        assert result.layer == "github_api"
        assert seen == ["https://api.github.com/repos/org/repo"]
    asyncio.run(run())


def test_fetch_does_not_follow_redirects():
    async def run():
        async def handler(request):
            return httpx.Response(302, headers={"location": "https://example.test/next"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError, match="redirect"):
                await WebAccessService(client).fetch("https://example.test")
    asyncio.run(run())


def test_retrieve_routes_site_api_and_rejects_ambiguous_input():
    async def run():
        async def handler(request):
            return httpx.Response(200, text='{}')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = WebAccessService(client)
            result = await service.retrieve(site="github", identifier="org/repo")
            assert result["layer"] == "site_api"
            with pytest.raises(ValueError, match="exactly one"):
                await service.retrieve(query="q", url="https://example.test")
    asyncio.run(run())
