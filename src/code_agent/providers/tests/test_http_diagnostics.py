from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from code_agent.providers.config import ApiProtocol, ProviderConfig
from code_agent.providers.errors import ProviderHTTPError
from code_agent.providers.transport import ProviderTransport


class DiagnosticStream(httpx.AsyncByteStream):
    def __init__(self, chunk: bytes, *, fail: bool = False) -> None:
        self.chunk = chunk
        self.fail = fail
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for _ in range(20):
            self.reads += 1
            if self.fail:
                raise httpx.ReadError('secret-key')
            yield self.chunk

    async def aclose(self) -> None:
        self.closed = True


class HTTPDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def failure(self, response, *, max_retries=0, max_bytes=65536):
        calls = []
        delays = []

        async def handler(request):
            calls.append(request)
            return response() if callable(response) else response

        async def sleep(delay):
            delays.append(delay)

        config = ProviderConfig(
            base_url='https://example.test', model='test',
            api=ApiProtocol.RESPONSES, api_key_env='TEST_KEY',
            max_retries=max_retries, max_response_bytes=max_bytes,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = ProviderTransport(config, client=client, sleep=sleep)
            with patch.dict('os.environ', {'TEST_KEY': 'secret-key'}):
                with self.assertRaises(ProviderHTTPError) as raised:
                    _ = [event async for event in transport.stream_sse('/responses', {})]
        return raised.exception, calls, delays

    async def test_html_502_is_short_and_retains_retry_status(self):
        streams = []

        def response():
            stream = DiagnosticStream(b'<html>secret-key</html>' * 10000)
            streams.append(stream)
            return httpx.Response(502, headers={'content-type': 'text/html'}, stream=stream)

        error, calls, delays = await self.failure(response, max_retries=2)
        self.assertEqual(str(error), 'Provider HTTP status 502 (Bad Gateway)')
        self.assertTrue(error.retryable)
        self.assertEqual(len(calls), 3)
        self.assertEqual(delays, [0.25, 0.5])
        self.assertTrue(all(stream.closed and stream.reads == 0 for stream in streams))

    async def test_unlabelled_html_is_not_rendered(self):
        error, _, _ = await self.failure(httpx.Response(502, content=b'<!DOCTYPE html>oops'))
        self.assertNotIn('<', str(error))
        self.assertNotIn('oops', str(error))

    async def test_error_body_stops_at_both_configured_and_diagnostic_limits(self):
        for limit, expected_reads in ((2048, 2), (65536, 8)):
            with self.subTest(limit=limit):
                stream = DiagnosticStream(b'x' * 1024)
                error, _, _ = await self.failure(httpx.Response(500, stream=stream), max_bytes=limit)
                self.assertEqual(stream.reads, expected_reads)
                self.assertTrue(stream.closed)
                self.assertEqual(str(error), 'Provider HTTP status 500 (Internal Server Error)')

    async def test_broken_diagnostic_does_not_replace_http_status(self):
        stream = DiagnosticStream(b'', fail=True)
        error, _, _ = await self.failure(httpx.Response(503, stream=stream))
        self.assertEqual(error.status, 503)
        self.assertTrue(error.retryable)
        self.assertNotIn('secret-key', str(error))
        self.assertTrue(stream.closed)

    async def test_json_message_is_useful_single_line_and_redacted(self):
        response = httpx.Response(400, json={'error': {
            'message': 'Invalid model\nsecret-key\tplease retry', 'token': 'hidden-token',
        }})
        error, _, _ = await self.failure(response)
        self.assertIn('Invalid model [REDACTED] please retry', str(error))
        self.assertNotIn('secret-key', str(error))
        self.assertNotIn('hidden-token', str(error))
        self.assertFalse(error.retryable)

    async def test_long_plain_error_is_bounded_and_control_codes_removed(self):
        error, _, _ = await self.failure(httpx.Response(400, text='\x1b[2J\n' + 'x' * 3000))
        self.assertLess(len(str(error)), 400)
        self.assertNotIn('\x1b', str(error))
        self.assertNotIn('\n', str(error))

    async def test_authentication_body_is_not_read(self):
        for status in (401, 403):
            with self.subTest(status=status):
                stream = DiagnosticStream(b'secret-key')
                error, calls, _ = await self.failure(httpx.Response(status, stream=stream), max_retries=2)
                self.assertEqual(len(calls), 1)
                self.assertEqual(stream.reads, 0)
                self.assertTrue(stream.closed)
                self.assertNotIn('secret-key', str(error))


if __name__ == '__main__':
    unittest.main()
