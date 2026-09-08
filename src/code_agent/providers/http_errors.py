"""Bounded, single-line diagnostics for unsuccessful HTTP responses."""

from __future__ import annotations

import json
from http import HTTPStatus

import httpx

from .errors import ProviderError, ProviderHTTPError


_ERROR_BODY_LIMIT = 8192
_DETAIL_LIMIT = 320


async def http_error(
    response: httpx.Response, *, retryable: bool, byte_limit: int, api_key: str
) -> ProviderHTTPError:
    """Keep the status even when a diagnostic body is absent, huge or broken."""
    status = response.status_code
    message = f"Provider HTTP status {status}"
    try:
        message += f" ({HTTPStatus(status).phrase})"
    except ValueError:
        pass
    content_type = response.headers.get("content-type", "").lower()
    if status not in {401, 403} and "html" not in content_type:
        body = await _read_error_body(response, min(byte_limit, _ERROR_BODY_LIMIT))
        detail = _body_detail(body, api_key)
        if detail:
            message += f": {detail}"
    return ProviderHTTPError(status=status, retryable=retryable, message=message)


async def _read_error_body(response: httpx.Response, limit: int) -> bytes:
    body = bytearray()
    try:
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) >= limit:
                # An incomplete diagnostic may cut a credential in half, so omit it.
                return b""
            body.extend(chunk)
    except httpx.TransportError:
        return b""
    return bytes(body)


def _body_detail(body: bytes, api_key: str) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    if not text or text.startswith("<"):
        return ""
    try:
        value = json.loads(text)
    except ValueError:
        value = text
    if isinstance(value, dict):
        value = value.get("error", value)
        if isinstance(value, dict):
            value = value.get("message", "")
    if not isinstance(value, str):
        return ""
    text = str(ProviderError(value, sensitive_values=(api_key,)))
    text = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    if "<html" in text.lower() or "<!doctype" in text.lower():
        return ""
    return text if len(text) <= _DETAIL_LIMIT else text[:_DETAIL_LIMIT] + "…"
