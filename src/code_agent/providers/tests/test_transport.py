from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.providers.config import ApiProtocol, ProviderConfig  # noqa: E402
from code_agent.providers.errors import (  # noqa: E402
    ProviderError,
    ProviderHTTPError,
    ProviderResponseLimitError,
)
from code_agent.providers.sse import SSEEvent  # noqa: E402
from code_agent.providers.transport import ProviderTransport  # noqa: E402


class FailingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        yield b"data: first\n\n"
        raise httpx.ReadError("Authorization: Bearer stream-secret")

    async def aclose(self) -> None:
        self.closed = True


class BlockingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False
        self.release = asyncio.Event()

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        yield b"data: first\n\n"
        await self.release.wait()

    async def aclose(self) -> None:
        self.closed = True
        self.release.set()


class ChunkListStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class ProviderTransportTests(unittest.IsolatedAsyncioTestCase):
    def make_config(self, **overrides: object) -> ProviderConfig:
        values = {
            "base_url": "https://api.example.test",
            "model": "model-1",
            "api": ApiProtocol.RESPONSES,
            "api_key_env": "TEST_PROVIDER_KEY",
            "timeout_s": 2.0,
            "max_retries": 2,
            "max_event_bytes": 1024,
        }
        values.update(overrides)
        return ProviderConfig(**values)  # type: ignore[arg-type]

    async def test_stream_posts_json_with_fresh_authorization_header(self) -> None:
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, content=b"data: ok\r\n\r\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transport = ProviderTransport(self.make_config(), client=client)
        with patch.dict("os.environ", {"TEST_PROVIDER_KEY": "secret-key"}, clear=True):
            events = [
                event
                async for event in transport.stream_sse(
                    "/v1/responses", {"stream": True, "value": 1}
                )
            ]

        self.assertEqual(events, [SSEEvent(data="ok")])
        self.assertEqual(seen[0].headers["authorization"], "Bearer secret-key")
        self.assertEqual(seen[0].url, httpx.URL("https://api.example.test/v1/responses"))
        self.assertEqual(seen[0].read(), b'{"stream":true,"value":1}')
        await transport.aclose()
        self.assertFalse(client.is_closed)
        await client.aclose()

    async def test_retries_429_and_5xx_with_bounded_exponential_backoff(self) -> None:
        statuses = iter((429, 503, 200))
        attempts = 0
        delays: list[float] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            status = next(statuses)
            return httpx.Response(status, content=b"data: done\n\n")

        async def sleep(delay: float) -> None:
            delays.append(delay)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transport = ProviderTransport(self.make_config(), client=client, sleep=sleep)
        with patch.dict("os.environ", {"TEST_PROVIDER_KEY": "key"}, clear=True):
            events = [event async for event in transport.stream_sse("/v1/responses", {})]

        self.assertEqual(events, [SSEEvent(data="done")])
        self.assertEqual(attempts, 3)
        self.assertEqual(delays, [0.25, 0.5])
        await client.aclose()

    async def test_auth_error_is_not_retried_or_leaked(self) -> None:
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(401, content=b'{"secret":"complete body"}')

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transport = ProviderTransport(self.make_config(), client=client)
        with patch.dict("os.environ", {"TEST_PROVIDER_KEY": "never-print-me"}, clear=True):
            with self.assertRaises(ProviderHTTPError) as raised:
                _ = [event async for event in transport.stream_sse("/v1/responses", {})]

        self.assertEqual(attempts, 1)
        self.assertEqual(raised.exception.status, 401)
        self.assertFalse(raised.exception.retryable)
        self.assertNotIn("never-print-me", str(raised.exception))
        self.assertNotIn("complete body", str(raised.exception))
        await client.aclose()

    async def test_stream_failure_after_event_is_not_retried_and_closes_response(self) -> None:
        stream = FailingStream()
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(200, stream=stream)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transport = ProviderTransport(self.make_config(), client=client)
        with patch.dict("os.environ", {"TEST_PROVIDER_KEY": "key"}, clear=True):
            received = []
            with self.assertRaises(ProviderError) as raised:
                async for event in transport.stream_sse("/v1/responses", {}):
                    received.append(event)

        self.assertEqual(received, [SSEEvent(data="first")])
        self.assertEqual(attempts, 1)
        self.assertTrue(stream.closed)
        self.assertNotIn("stream-secret", str(raised.exception))
        await client.aclose()

    async def test_closing_generator_closes_response_and_internal_client_is_owned(self) -> None:
        stream = BlockingStream()

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=stream)

        external = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transport = ProviderTransport(self.make_config(), client=external)
        with patch.dict("os.environ", {"TEST_PROVIDER_KEY": "key"}, clear=True):
            generator = transport.stream_sse("/v1/responses", {})
            self.assertEqual(await anext(generator), SSEEvent(data="first"))
            await generator.aclose()
        self.assertTrue(stream.closed)
        await transport.aclose()
        self.assertFalse(external.is_closed)
        await external.aclose()

        owned = await asyncio.to_thread(ProviderTransport, self.make_config())
        self.assertFalse(owned.is_closed)
        await owned.aclose()
        self.assertTrue(owned.is_closed)

    async def test_event_limit_error_crosses_transport_without_vendor_types(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"data: 123456789\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transport = ProviderTransport(self.make_config(max_event_bytes=8), client=client)
        with patch.dict("os.environ", {"TEST_PROVIDER_KEY": "key"}, clear=True):
            with self.assertRaises(ProviderResponseLimitError):
                _ = [event async for event in transport.stream_sse("/v1/responses", {})]
        await client.aclose()

    async def test_cross_origin_redirect_is_never_followed_or_disclosed(self) -> None:
        requests: list[httpx.Request] = []
        leaked_key = False

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal leaked_key
            requests.append(request)
            if request.url.host == "redirect.example.test":
                leaked_key = request.headers.get("x-api-key") == "redirect-secret"
                return httpx.Response(200, content=b"data: followed\n\n")
            return httpx.Response(
                307,
                headers={"Location": "https://redirect.example.test/collect"},
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        )
        transport = ProviderTransport(self.make_config(), client=client)
        raised: ProviderHTTPError | None = None
        with patch.dict(
            "os.environ", {"TEST_PROVIDER_KEY": "redirect-secret"}, clear=True
        ):
            try:
                _ = [
                    item
                    async for item in transport.stream_sse(
                        "/v1/messages",
                        {},
                        auth_header="x-api-key",
                        auth_scheme=None,
                    )
                ]
            except ProviderHTTPError as error:
                raised = error

        self.assertFalse(leaked_key)
        self.assertEqual(len(requests), 1)
        self.assertIsNotNone(raised)
        assert raised is not None
        self.assertEqual(raised.status, 307)
        self.assertFalse(raised.retryable)
        rendered = str(raised)
        self.assertNotIn("redirect-secret", rendered)
        self.assertNotIn("x-api-key", rendered.lower())
        self.assertNotIn("redirect.example.test", rendered)
        await client.aclose()

    async def test_cumulative_response_bytes_are_bounded_and_stream_is_closed(self) -> None:
        stream = ChunkListStream([b"data: one\n\n", b"data: two\n\n"])

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=stream)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transport = ProviderTransport(
            self.make_config(max_response_bytes=16), client=client
        )
        received: list[SSEEvent] = []
        with patch.dict("os.environ", {"TEST_PROVIDER_KEY": "key"}, clear=True):
            with self.assertRaises(ProviderResponseLimitError):
                async for item in transport.stream_sse("/v1/responses", {}):
                    received.append(item)

        self.assertEqual(received, [SSEEvent(data="one")])
        self.assertTrue(stream.closed)
        await client.aclose()


if __name__ == "__main__":
    unittest.main()
