"""Bounded, explicit OpenAI-compatible model discovery."""
from __future__ import annotations

import json
import httpx

from .config import ProviderConfig
from .errors import ProviderError


async def discover_models(config: ProviderConfig, *, client: httpx.AsyncClient | None = None) -> tuple[str, ...]:
    """Read model IDs only; limits and capabilities remain configuration facts."""
    owned = client is None
    transport = client or httpx.AsyncClient(timeout=min(config.timeout_s, 30))
    try:
        key = config.resolve_api_key()
        base = config.base_url.rstrip("/")
        url = base + ("/models" if base.endswith("/v1") else "/v1/models")
        async with transport.stream("GET", url, headers={"Authorization": "Bearer " + key},
                                    follow_redirects=False, timeout=min(config.timeout_s, 30)) as response:
            if response.status_code != 200:
                raise ProviderError(f"Model discovery failed (HTTP {response.status_code})")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > min(config.max_response_bytes, 2 * 1024 * 1024):
                    raise ProviderError("Model catalog exceeds the response limit")
                body.extend(chunk)
        try:
            document = json.loads(body)
        except (ValueError, UnicodeError):
            raise ProviderError("Invalid model catalog JSON") from None
        data = document.get("data") if isinstance(document, dict) else None
        if not isinstance(data, list) or len(data) > 10000:
            raise ProviderError("Invalid model catalog data")
        ids = []
        for row in data:
            value = row.get("id") if isinstance(row, dict) else None
            if (isinstance(value, str) and value.strip() == value and value
                    and len(value) <= 256 and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
                ids.append(value)
        if not ids:
            raise ProviderError("Model catalog contains no usable model IDs")
        return tuple(sorted(set(ids)))
    except httpx.HTTPError:
        raise ProviderError("Model discovery connection failed") from None
    finally:
        if owned:
            await transport.aclose()
