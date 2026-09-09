"""Public OAuth login/refresh dispatch; protocols derived from uri-agent (MIT)."""
from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping

import httpx

from .flows_antigravity import login_antigravity, refresh_antigravity
from .flows_browser import login_browser, refresh_browser
from .flows_common import bounded
from .flows_device import login_device, refresh_device
from .flows_workbuddy import login_workbuddy, refresh_workbuddy
from .models import AuthError, Credential

METHODS = {"antigravity": ("oauth",), "anthropic": ("oauth",), "workbuddy": ("workbuddy",),
           "openrouter": ("oauth",), "openai-codex": ("browser", "device_code"),
           "github-copilot": ("oauth",), "kimi-coding": ("oauth",), "muse-code": ("oauth",),
           "xai": ("oauth",), "radius": ("browser", "device_code")}


async def login(provider: str, *, method: str | None = None, options: Mapping[str, str] | None = None,
                client: httpx.AsyncClient | None = None, display: Callable[[str], None] = print,
                read_input: Callable[[str], Awaitable[str]] | None = None,
                cancel: asyncio.Event | None = None, timeout: float = 600) -> Credential:
    """Authenticate without reading or persisting credentials; caller saves the result."""
    if provider not in METHODS:
        raise AuthError("Unsupported OAuth provider")
    method = method or METHODS[provider][0]
    if method not in METHODS[provider]:
        raise AuthError("Unsupported OAuth login method")
    if not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
        raise AuthError("OAuth timeout must be positive")
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await login(provider, method=method, options=options, client=owned, display=display,
                               read_input=read_input, cancel=cancel, timeout=timeout)
    flow = _login(provider, method, dict(options or {}), client, display, read_input)
    return await bounded(flow, min(timeout, 300) if provider == "workbuddy" else timeout, cancel)


async def _login(provider, method, options, client, display, read_input):
    if provider == "antigravity":
        return await login_antigravity(client, display, read_input)
    if provider == "workbuddy":
        return await login_workbuddy(client, display)
    if provider in {"anthropic", "openrouter"} or method == "browser":
        return await login_browser(provider, client, options, display, read_input)
    return await login_device(provider, client, options, display)


async def refresh(provider: str, credential: Credential, *, client: httpx.AsyncClient | None = None) -> Credential:
    """Refresh using the same issuer, retaining refresh tokens omitted by rotation."""
    if provider not in METHODS or credential.kind != "oauth":
        raise AuthError("Unsupported OAuth credential")
    if provider == "openrouter":
        return credential
    if provider == "muse-code":
        raise AuthError("Muse Code credentials cannot refresh; sign in again")
    if not credential.refresh:
        raise AuthError("OAuth refresh token missing; sign in again")
    if client is None:
        async with httpx.AsyncClient() as owned:
            return await refresh(provider, credential, client=owned)
    if provider == "antigravity":
        return await refresh_antigravity(client, credential)
    if provider == "workbuddy":
        return await refresh_workbuddy(client, credential)
    if provider in {"anthropic", "openai-codex", "radius"}:
        return await refresh_browser(provider, credential, client)
    return await refresh_device(provider, credential, client)
