"""OAuth primitives adapted from uri-agent src/oauth (MIT; see THIRD_PARTY_NOTICES)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import secrets
import time
import webbrowser
from dataclasses import replace
from urllib.parse import urlsplit

import httpx

from .models import AuthError, Credential


def required(value: dict, key: str) -> str:
    """Extract a nonempty protocol field without echoing server data."""
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise AuthError(f"OAuth response missing {key}")
    return result


def safe_url(value: str) -> str:
    """Permit HTTPS and local HTTP endpoints, without embedded credentials."""
    try:
        parts = urlsplit(value)
        valid = parts.scheme == "https" or (
            parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        if (not valid or not parts.hostname or parts.username is not None or parts.password is not None
                or "#" in value or any(ord(char) < 32 for char in value)):
            raise ValueError
        _ = parts.port
    except (ValueError, TypeError):
        raise AuthError("Invalid OAuth endpoint URL") from None
    return value


async def request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """Make a request with bounded, credential-free errors and no redirects."""
    try:
        return await client.request(method, safe_url(url), follow_redirects=False,
                                    timeout=30, **kwargs)
    except (httpx.HTTPError, ValueError):
        raise AuthError("OAuth network request failed") from None


def response_json(response: httpx.Response, *, check: bool = True) -> dict:
    if check and not response.is_success:
        raise AuthError(f"OAuth request failed (HTTP {response.status_code})")
    try:
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError):
        raise AuthError("OAuth response is not a JSON object") from None


def token(value: dict, previous: Credential | None = None, *, skew: float = 300) -> Credential:
    access = required(value, "access_token")
    refresh = value.get("refresh_token") or (previous.refresh if previous else "")
    if not isinstance(refresh, str):
        raise AuthError("Invalid OAuth refresh token field")
    try:
        duration = float(value.get("expires_in", 3600))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
    except (ValueError, TypeError):
        raise AuthError("Invalid OAuth expiry field") from None
    extra = dict(previous.extra) if previous else {}
    if isinstance(value.get("scope"), str):
        extra["scope"] = value["scope"]
    return Credential("oauth", access, refresh, time.time() + duration - min(skew, duration / 2), extra)


def with_extra(credential: Credential, **extra) -> Credential:
    return replace(credential, extra={**credential.extra, **extra})


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def account_id(access: str) -> str:
    """Read account routing metadata; JWT signature verification belongs to issuer."""
    try:
        payload = access.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return required(value["https://api.openai.com/auth"], "chatgpt_account_id")
    except (ValueError, IndexError, KeyError, TypeError):
        raise AuthError("Codex token has no account routing identity") from None


async def show(url: str, display, user_code: str | None = None) -> None:
    url = safe_url(url)
    display(f"请在浏览器完成登录：{url}" + (f"\n设备码：{user_code}" if user_code else ""))
    await asyncio.to_thread(webbrowser.open, url)


async def bounded(awaitable, timeout: float, cancel: asyncio.Event | None = None):
    """Cancel the underlying flow on deadline or caller cancellation."""
    if cancel is not None and cancel.is_set():
        awaitable.close()
        raise AuthError("OAuth login cancelled")
    task = asyncio.create_task(awaitable)
    cancellation = asyncio.create_task(cancel.wait()) if cancel is not None else None
    try:
        done, _ = await asyncio.wait(
            {task, cancellation} if cancellation else {task}, timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancellation is not None and cancellation in done:
            raise AuthError("OAuth login cancelled")
        if task not in done:
            raise AuthError("OAuth login timed out")
        return await task
    finally:
        for pending in (task, cancellation):
            if pending is not None and not pending.done():
                pending.cancel()
        await asyncio.gather(*(p for p in (task, cancellation) if p), return_exceptions=True)
