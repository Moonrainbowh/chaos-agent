import unittest
import httpx
from code_agent.web_access.service import WebAccessService


class BoundedAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_challenge_is_reported_instead_of_empty_results(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(202,text="anomaly"))) as client:
            with self.assertRaisesRegex(RuntimeError,"verification"):
                await WebAccessService(client).search("q")
    async def test_compressed_response_is_decoded_exactly_once(self):
        import gzip
        compressed = gzip.compress(b"<title>Compressed page</title>body")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,
                headers={"Content-Encoding":"gzip"},content=compressed))) as client:
            result = await WebAccessService(client).fetch("https://example.com")
        self.assertEqual(result.title,"Compressed page")
    async def test_search_unwraps_provider_redirects_to_source(self):
        body = '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle">Source</a>'
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,text=body))) as client:
            result = await WebAccessService(client).search("q")
        self.assertEqual(result[0]["url"],"https://example.com/article")

    async def test_fetch_marks_bounded_body_and_http_failure_is_not_empty_success(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b"x"*4096))) as client:
            result = await WebAccessService(client,max_bytes=1024).fetch("https://example.com")
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.content),1024)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(403))) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                await WebAccessService(client).search("q")
