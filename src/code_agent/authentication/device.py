"""RFC 8628 polling with provider-specific pending statuses (uri-agent, MIT)."""
from __future__ import annotations

import asyncio
import math
import time

from .flows_common import request, response_json
from .models import AuthError


async def poll(client, url, data, *, setup=None, headers=None, json_body=False,
               pending_statuses=(), success_key="access_token", immediate=False):
    setup = setup or {}
    try:
        interval = max(1.0, float(setup.get("interval", 5)))
        lifetime = min(900.0, float(setup.get("expires_in", 900)))
        if not math.isfinite(interval) or not math.isfinite(lifetime) or lifetime <= 0:
            raise ValueError
    except (ValueError, TypeError):
        raise AuthError("Invalid device authorization timing") from None
    deadline = time.monotonic() + lifetime
    while time.monotonic() < deadline:
        if not immediate:
            await asyncio.sleep(min(interval, max(0, deadline - time.monotonic())))
        immediate = False
        if time.monotonic() >= deadline:
            break
        response = await request(client, "POST", url, headers=headers or {},
                                 **({"json": data} if json_body else {"data": data}))
        try:
            value = response_json(response, check=False)
        except AuthError:
            if response.status_code in pending_statuses:
                continue
            raise
        if response.is_success and value.get(success_key):
            return value
        error = value.get("error")
        if isinstance(error, dict):
            error = error.get("code")
        if error is not None and not isinstance(error, str):
            raise AuthError("Invalid OAuth device error response")
        if error in {"authorization_pending", "deviceauth_authorization_pending"}:
            continue
        if error == "slow_down":
            interval += 5
            continue
        if response.status_code in pending_statuses and error is None:
            continue
        if error in {"access_denied", "authorization_denied"}:
            raise AuthError("OAuth authorization denied")
        if error == "expired_token":
            raise AuthError("OAuth device authorization expired")
        raise AuthError(f"OAuth device request failed (HTTP {response.status_code})")
    raise AuthError("OAuth device authorization expired")
