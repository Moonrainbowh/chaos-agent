"""Experimental Antigravity OAuth and project onboarding (uri-agent, MIT)."""
from __future__ import annotations

import asyncio
import os
import secrets
from urllib.parse import urlencode

from .callback import BrowserCallback
from .flows_common import pkce, request, required, response_json, token, with_extra
from .models import AuthError

TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT = "http://localhost:8085/callback"
BASES = ("https://daily-cloudcode-pa.sandbox.googleapis.com", "https://daily-cloudcode-pa.googleapis.com",
         "https://cloudcode-pa.googleapis.com")
SCOPES = ("openid https://www.googleapis.com/auth/cloud-platform https://www.googleapis.com/auth/userinfo.email "
          "https://www.googleapis.com/auth/userinfo.profile https://www.googleapis.com/auth/cclog "
          "https://www.googleapis.com/auth/experimentsandconfigs")


def identity():
    """Require caller-configured OAuth application identity without bundled values."""
    names = ("ANTIGRAVITY_OAUTH_CLIENT_ID", "ANTIGRAVITY_OAUTH_CLIENT_SECRET")
    values = tuple((os.getenv(name) or "").strip() for name in names)
    missing = [name for name, value in zip(names, values) if not value]
    if missing:
        raise AuthError("Antigravity OAuth requires environment variables: " + ", ".join(missing))
    return values


def headers(access=None):
    version = os.getenv("ANTIGRAVITY_USER_AGENT_VERSION") or "4.3.0"
    result = {"User-Agent": f"vscode/1.X.X (Antigravity/{version})"}
    if access:
        result["Authorization"] = "Bearer " + access
    return result


async def login_antigravity(client, display, read_input):
    client_id, secret = identity()
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(24)
    fields = {"client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPES,
              "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
              "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"}
    async with BrowserCallback(REDIRECT, state) as callback:
        code = await callback.receive("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(fields), display, read_input)
    response = await request(client, "POST", TOKEN_URL, headers=headers(), data={
        "client_id": client_id, "client_secret": secret, "code": code, "redirect_uri": REDIRECT,
        "grant_type": "authorization_code", "code_verifier": verifier,
    })
    credential = with_extra(token(response_json(response), skew=900), oauthClientId=client_id)
    return await enrich(client, credential)


async def refresh_antigravity(client, credential):
    client_id, secret = identity()
    if credential.extra.get("oauthClientId", client_id) != client_id:
        raise AuthError("Antigravity OAuth client changed; restore client configuration or sign in again")
    value = None
    for attempt in range(2):
        response = await request(client, "POST", TOKEN_URL, headers=headers(), data={
            "client_id": client_id, "client_secret": secret,
            "grant_type": "refresh_token", "refresh_token": credential.refresh,
        })
        value = response_json(response, check=False)
        if response.is_success:
            break
        if attempt == 0 and value.get("error") == "invalid_grant":
            await asyncio.sleep(0.5)
            continue
        raise AuthError(f"Antigravity refresh failed (HTTP {response.status_code})")
    updated = with_extra(token(value, credential, skew=900), oauthClientId=client_id)
    try:
        return await enrich(client, updated)
    except AuthError:
        if credential.extra.get("projectId"):
            return updated
        raise


def project(value):
    result = value.get("cloudaicompanionProject")
    if isinstance(result, dict):
        result = result.get("id")
    return result.strip() if isinstance(result, str) and result.strip() else None


async def control(client, access, action, body, bases=BASES):
    for index, base in enumerate(bases):
        try:
            response = await request(client, "POST", f"{base}/v1internal:{action}", headers=headers(access), json=body)
        except AuthError:
            if index + 1 < len(bases):
                continue
            raise
        if response.status_code in {404, 408, 429} or response.status_code >= 500:
            if index + 1 < len(bases):
                continue
        return response_json(response), base
    raise AuthError("Antigravity project request failed")


async def discover(client, access):
    for attempt in range(3):
        if attempt:
            await asyncio.sleep(2 ** (attempt - 1))
        value, base = await control(client, access, "loadCodeAssist", {"metadata": {
            "ideType": "ANTIGRAVITY", "ideName": "antigravity",
            "ideVersion": os.getenv("ANTIGRAVITY_USER_AGENT_VERSION") or "4.3.0",
        }})
        tier = value.get("paidTier") or value.get("currentTier")
        if isinstance(tier, dict):
            tier = tier.get("id")
        if project(value):
            return project(value), tier
        allowed = value.get("allowedTiers", [])
        selected = next((t.get("id") for t in allowed if isinstance(t, dict) and t.get("isDefault")), None)
        if not selected:
            raise AuthError("Antigravity returned no project or default tier")
        for _ in range(5):
            result, _ = await control(client, access, "onboardUser", {"tierId": selected, "metadata": {
                "ideType": "ANTIGRAVITY", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI",
            }}, (base,) + tuple(b for b in BASES if b != base))
            if result.get("done"):
                found = project(result.get("response", {}))
                if not found:
                    raise AuthError("Antigravity onboarding returned no project")
                return found, tier
            await asyncio.sleep(2)
    raise AuthError("Antigravity onboarding did not complete")


async def enrich(client, credential):
    extra = {}
    try:
        value = response_json(await request(client, "GET", "https://www.googleapis.com/oauth2/v2/userinfo",
                                            headers=headers(credential.access)))
        if isinstance(value.get("email"), str):
            extra["email"] = value["email"]
    except AuthError:
        pass
    project_id, tier = await discover(client, credential.access)
    extra["projectId"] = project_id
    if isinstance(tier, str):
        extra["tier"] = tier
    return with_extra(credential, **extra)
