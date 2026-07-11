from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.providers.config import ApiProtocol, ProviderConfig  # noqa: E402
from code_agent.providers.errors import ProviderError  # noqa: E402
from code_agent.providers.sse import SSEEvent  # noqa: E402
from code_agent.providers.transport import ProviderTransport  # noqa: E402


def config() -> ProviderConfig:
    return ProviderConfig(
        base_url="https://api.example.test",
        model="model-1",
        api=ApiProtocol.RESPONSES,
        api_key_env="RETRY_KEY",
        max_retries=2,
        max_event_bytes=1024,
    )


class TransportRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_and_write_failures_before_first_event_are_not_retried(self) -> None:
        error_types = (httpx.ReadTimeout, httpx.ReadError, httpx.WriteError)

        for error_type in error_types:
            with self.subTest(error_type=error_type.__name__):
                attempts = 0
                delays: list[float] = []

                async def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal attempts
                    attempts += 1
                    raise error_type(
                        "Authorization: Bearer retry-secret", request=request
                    )

                async def sleep(delay: float) -> None:
                    delays.append(delay)

                client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                transport = ProviderTransport(config(), client=client, sleep=sleep)
                with patch.dict(
                    "os.environ", {"RETRY_KEY": "retry-secret"}, clear=True
                ):
                    with self.assertRaises(ProviderError) as raised:
                        _ = [
                            item
                            async for item in transport.stream_sse(
                                "/v1/responses", {}
                            )
                        ]

                self.assertEqual(attempts, 1)
                self.assertEqual(delays, [])
                self.assertNotIn("retry-secret", str(raised.exception))
                await client.aclose()

    async def test_connection_and_pool_failures_retry_before_first_event(self) -> None:
        error_types = (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.PoolTimeout,
        )

        for error_type in error_types:
            with self.subTest(error_type=error_type.__name__):
                attempts = 0
                delays: list[float] = []

                async def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal attempts
                    attempts += 1
                    if attempts < 3:
                        raise error_type("temporary connection failure", request=request)
                    return httpx.Response(200, content=b"data: done\n\n")

                async def sleep(delay: float) -> None:
                    delays.append(delay)

                client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                transport = ProviderTransport(config(), client=client, sleep=sleep)
                with patch.dict("os.environ", {"RETRY_KEY": "key"}, clear=True):
                    events = [
                        item
                        async for item in transport.stream_sse(
                            "/v1/responses", {}
                        )
                    ]

                self.assertEqual(events, [SSEEvent(data="done")])
                self.assertEqual(attempts, 3)
                self.assertEqual(delays, [0.25, 0.5])
                await client.aclose()


if __name__ == "__main__":
    unittest.main()
