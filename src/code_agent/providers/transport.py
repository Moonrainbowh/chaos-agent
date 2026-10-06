from __future__ import annotations

import asyncio
import json
from contextlib import aclosing
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Optional

import httpx
from code_agent.authentication.models import AuthError, Credential

from .config import ProviderConfig
from .errors import ProviderError, ProviderResponseLimitError
from .http_errors import http_error
from .sse import SSEDecoder, SSEEvent
from .prepared import PreparedProviderRequest


Sleep = Callable[[float], Awaitable[None]]
_RETRYABLE_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)


class ProviderTransport:
    """Own HTTP details and expose only decoded provider-level SSE events."""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: Optional[httpx.AsyncClient] = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._config = config
        self._client = client or httpx.AsyncClient(timeout=config.timeout_s)
        self._owns_client = client is None
        self._sleep = sleep
        self._closed = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> ProviderTransport:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def stream_sse(
        self,
        path: str,
        payload: Mapping[str, object],
        headers: Optional[Mapping[str, str]] = None,
        *,
        auth_header: str = "Authorization",
        auth_scheme: Optional[str] = "Bearer",
    ) -> AsyncIterator[SSEEvent]:
        prepared = await self.prepare_request(path, payload, headers,
            auth_header=auth_header, auth_scheme=auth_scheme)
        async with aclosing(self.stream_prepared(prepared)) as events:
            async for event in events:
                yield event

    async def prepare_request(self, path, payload, headers=None, *,
                              auth_header="Authorization", auth_scheme="Bearer",
                              max_output_tokens=0, uncalibrated_images=False):
        """Resolve transport authentication once and freeze the transformed JSON body."""
        if self._closed:
            raise ProviderError("Provider transport is closed")
        try:
            credential = (
                await self._config.auth_source.resolve()
                if self._config.auth_source is not None
                else Credential("api_key", self._config.resolve_api_key())
            )
        except AuthError as error:
            raise ProviderError(str(error)) from None
        api_key = credential.access
        request_headers = {"Accept": "text/event-stream"}
        if headers:
            request_headers.update(headers)
        request_headers[auth_header] = (
            f"{auth_scheme} {api_key}" if auth_scheme else api_key
        )
        url = f"{self._config.base_url}{path}"
        if self._config.provider_id:
            from .auth_request import authenticated_request
            url, payload, request_headers = authenticated_request(
                self._config, credential, path, payload, request_headers
            )
        enforced = self._config.provider_id != "openai-codex"
        diagnostics = () if enforced else (
            "protocol omits remote output limit; configured reserve is local only",)
        request_headers.setdefault("Content-Type", "application/json")
        body = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
        return PreparedProviderRequest(body, url, tuple(request_headers.items()),
            max_output_tokens, enforced, uncalibrated_images, diagnostics,
            self, (api_key,))

    async def stream_prepared(self, prepared):
        """Send exactly the admitted body, including on retries, without re-encoding."""
        if self._closed:
            raise ProviderError("Provider transport is closed")
        if not isinstance(prepared, PreparedProviderRequest) or prepared._transport_identity is not self:
            raise ProviderError("prepared request belongs to another transport")
        url, request_headers = prepared.url, dict(prepared.headers)
        api_key = prepared._sensitive_values[0]
        attempt = 0

        while True:
            emitted = False
            retry_delay: Optional[float] = None
            try:
                async with self._client.stream(
                    "POST",
                    url,
                    headers=request_headers,
                    content=prepared.body,
                    follow_redirects=False,
                    timeout=self._config.timeout_s,
                ) as response:
                    if not response.is_success:
                        retryable = response.status_code == 429 or (
                            500 <= response.status_code <= 599
                        )
                        if retryable and attempt < self._config.max_retries:
                            retry_delay = self._backoff(attempt)
                        else:
                            raise await http_error(
                                response,
                                retryable=retryable,
                                byte_limit=self._config.max_response_bytes,
                                api_key=api_key,
                            )
                    else:
                        decoder = SSEDecoder(self._config.max_event_bytes)
                        response_bytes = 0
                        async for chunk in response.aiter_bytes():
                            response_bytes += len(chunk)
                            if response_bytes > self._config.max_response_bytes:
                                raise ProviderResponseLimitError(
                                    "Provider response exceeded its byte limit"
                                )
                            for event in decoder.feed(chunk):
                                emitted = True
                                yield event
                        for event in decoder.finalize():
                            emitted = True
                            yield event
            except _RETRYABLE_TRANSPORT_ERRORS as error:
                if not emitted and attempt < self._config.max_retries:
                    retry_delay = self._backoff(attempt)
                else:
                    raise ProviderError(
                        self._format_transport_error(error, url),
                        sensitive_values=(api_key,),
                        effect_unknown=True,
                        retryable=True,
                    ) from None
            except httpx.TransportError as error:
                raise ProviderError(
                    self._format_transport_error(error, url),
                    sensitive_values=(api_key,),
                    effect_unknown=True,
                    retryable=isinstance(
                        error, (httpx.TimeoutException, httpx.NetworkError)
                    ),
                ) from None

            if retry_delay is None:
                return
            await self._sleep(retry_delay)
            attempt += 1

    @staticmethod
    def _format_transport_error(error: BaseException, url: str) -> str:
        from urllib.parse import urlsplit
        host = urlsplit(url).hostname or url
        error_name = type(error).__name__
        details = " ".join(str(error).split()) if str(error) else ""
        if details:
            details = f": {details[:80]}"
        hint = ""
        if isinstance(
            error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError)
        ):
            hint = " (check network or proxy settings)"
        return f"Provider transport failure: {error_name} ({host}){details}{hint}"

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(0.25 * (2**attempt), 4.0)
