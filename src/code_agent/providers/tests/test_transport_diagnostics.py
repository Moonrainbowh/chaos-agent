from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.providers.config import ApiProtocol, ProviderConfig
from code_agent.providers.errors import ProviderError
from code_agent.providers.transport import ProviderTransport
from code_agent.interfaces.task_controller import _is_recoverable_model_failure


def _make_config() -> ProviderConfig:
    return ProviderConfig(
        base_url="https://cloudcode-pa.googleapis.com",
        model="gemini-3.8-flash-high",
        api=ApiProtocol.RESPONSES,
        api_key_env="TEST_KEY",
        max_retries=1,
        max_event_bytes=1024,
    )


class TransportDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_error_includes_host_and_hint_and_is_retryable(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("[WinError 10061] Connection refused", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transport = ProviderTransport(_make_config(), client=client)

        with patch.dict("os.environ", {"TEST_KEY": "super-secret-key"}, clear=True):
            with self.assertRaises(ProviderError) as raised:
                _ = [event async for event in transport.stream_sse("/v1internal", {})]

        error = raised.exception
        self.assertTrue(error.retryable)
        self.assertTrue(_is_recoverable_model_failure(error))

        rendered = str(error)
        self.assertIn("ConnectError", rendered)
        self.assertIn("cloudcode-pa.googleapis.com", rendered)
        self.assertIn("check network or proxy settings", rendered)
        self.assertNotIn("super-secret-key", rendered)
        await client.aclose()

    async def test_connect_timeout_is_retryable_with_host(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("Connection timed out", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transport = ProviderTransport(_make_config(), client=client)

        with patch.dict("os.environ", {"TEST_KEY": "key"}, clear=True):
            with self.assertRaises(ProviderError) as raised:
                _ = [event async for event in transport.stream_sse("/v1internal", {})]

        error = raised.exception
        self.assertTrue(error.retryable)
        self.assertIn("ConnectTimeout", str(error))
        self.assertIn("cloudcode-pa.googleapis.com", str(error))
        await client.aclose()

    async def test_sensitive_credentials_in_error_are_redacted(self) -> None:
        secret = "secret-token-12345"

        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"failed with token {secret}", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transport = ProviderTransport(_make_config(), client=client)

        with patch.dict("os.environ", {"TEST_KEY": secret}, clear=True):
            with self.assertRaises(ProviderError) as raised:
                _ = [event async for event in transport.stream_sse("/v1internal", {})]

        rendered = str(raised.exception)
        self.assertNotIn(secret, rendered)
        self.assertIn("[REDACTED]", rendered)
        await client.aclose()


if __name__ == "__main__":
    unittest.main()
