"""Subscription device flows ported from uri-agent providers (MIT)."""
from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit

from .device import poll
from .flows_browser import CODEX_ID, exchange_codex, gateway
from .flows_common import request, required, response_json, safe_url, show, token, with_extra
from .models import AuthError, Credential

GRANT = "urn:ietf:params:oauth:grant-type:device_code"
KIMI_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"
XAI_ID = "b1a00492-073a-47ea-816f-4c329264a828"
COPILOT_ID = "Iv1.b507a08c87ecfe98"
COPILOT_HEADERS = {"User-Agent": "GitHubCopilotChat/0.35.0", "Accept": "application/json"}


def kimi_host():
    return safe_url(os.getenv("KIMI_CODE_OAUTH_HOST") or os.getenv("KIMI_OAUTH_HOST") or "https://auth.kimi.com").rstrip("/")


def device_config(provider, options):
    if provider == "kimi-coding":
        host = kimi_host()
        return host + "/api/oauth/device_authorization", host + "/api/oauth/token", {"client_id": KIMI_ID}, {}
    if provider == "xai":
        return "https://auth.x.ai/oauth2/device/code", "https://auth.x.ai/oauth2/token", {
            "client_id": XAI_ID, "scope": "openid profile email offline_access grok-cli:access api:access", "referrer": "pi",
        }, {}
    if provider == "radius":
        host = gateway(options)
        return host + "/v1/oauth/device", host + "/v1/oauth/token", {
            "client_id": "pi-gateway", "scope": "gateway offline_access",
        }, {}
    if provider == "muse-code":
        return "https://auth.meta.com/oidc/device/authorization/", "https://auth.meta.com/oidc/device/token/", {
            "client_id": "1031625952748946",
        }, {"x-api-version": "1.0.0"}
    domain = github_domain(options.get("domain", "github.com"))
    return f"https://{domain}/login/device/code", f"https://{domain}/login/oauth/access_token", {
        "client_id": COPILOT_ID, "scope": "read:user",
    }, COPILOT_HEADERS


def github_domain(value):
    return urlsplit(safe_url(value if "://" in value else "https://" + value)).netloc


async def login_device(provider, client, options, display):
    if provider == "openai-codex":
        return await codex_device(client, display)
    device_url, token_url, body, headers = device_config(provider, options)
    headers = {"Accept": "application/json", **headers}
    setup = response_json(await request(client, "POST", device_url, data=body, headers=headers))
    verification = safe_url(setup.get("verification_uri_complete") or required(setup, "verification_uri"))
    if provider == "muse-code":
        parts = urlsplit(verification)
        host = parts.hostname
        if (parts.scheme != "https" or parts.port not in {None, 443}
                or (host != "meta.com" and not host.endswith(".meta.com"))):
            raise AuthError("Untrusted Meta verification URL")
    await show(verification, display, required(setup, "user_code"))
    value = await poll(client, token_url, {"client_id": body["client_id"],
                       "device_code": required(setup, "device_code"), "grant_type": GRANT},
                       setup=setup, headers=headers)
    if provider == "github-copilot":
        return await copilot_token(client, required(value, "access_token"), github_domain(options.get("domain", "github.com")))
    if provider == "muse-code":
        return await muse_key(client, required(value, "access_token"))
    result = token(value)
    return with_extra(result, gateway=gateway(options)) if provider == "radius" else result


async def codex_device(client, display):
    setup = response_json(await request(client, "POST", "https://auth.openai.com/api/accounts/deviceauth/usercode",
                                        json={"client_id": CODEX_ID}))
    user_code = required(setup, "user_code")
    await show("https://auth.openai.com/codex/device", display, user_code)
    value = await poll(client, "https://auth.openai.com/api/accounts/deviceauth/token", {
        "device_auth_id": required(setup, "device_auth_id"), "user_code": user_code,
    }, setup=setup, json_body=True, pending_statuses=(403, 404), success_key="authorization_code", immediate=True)
    return await exchange_codex(client, required(value, "authorization_code"), required(value, "code_verifier"),
                                "https://auth.openai.com/deviceauth/callback")


async def copilot_token(client, github_token, domain):
    headers = {**COPILOT_HEADERS, "Authorization": f"Bearer {github_token}",
               "Editor-Version": "vscode/1.107.0", "Editor-Plugin-Version": "copilot-chat/0.35.0",
               "Copilot-Integration-Id": "vscode-chat"}
    value = response_json(await request(client, "GET", f"https://api.{domain}/copilot_internal/v2/token", headers=headers))
    try:
        expiry = float(value["expires_at"]) - 300
    except (KeyError, TypeError, ValueError):
        raise AuthError("Invalid Copilot token expiry") from None
    extra = {"enterpriseUrl": domain} if domain != "github.com" else {}
    return Credential("oauth", required(value, "token"), github_token, expiry, extra)


async def muse_key(client, account_token):
    value = response_json(await request(client, "POST", "https://api.meta.ai/muse-code/key", json={"onboard": True},
                          headers={"x-api-version": "1.0.0", "Authorization": f"Bearer {account_token}"}))
    if value.get("is_subs_active") is False:
        raise AuthError("Muse Code subscription inactive; sign in again after subscribing")
    if not value.get("api_key") and value.get("require_payment"):
        raise AuthError("Muse Code subscription required")
    identity = value.get("user_id") or value.get("user_email")
    if not isinstance(identity, str) or not identity.strip():
        raise AuthError("Muse Code key has no account identity")
    extra = {"accountId": identity, "oauthAccessToken": account_token}
    if isinstance(value.get("user_email"), str):
        extra["email"] = value["user_email"].lower()
    return Credential("oauth", required(value, "api_key"), extra=extra)


async def refresh_device(provider, credential, client):
    if provider == "github-copilot":
        return await copilot_token(client, credential.refresh, github_domain(credential.extra.get("enterpriseUrl", "github.com")))
    _, url, body, headers = device_config(provider, {})
    attempts = 4 if provider == "kimi-coding" else 1
    for attempt in range(attempts):
        try:
            response = await request(client, "POST", url, headers=headers, data={
                "grant_type": "refresh_token", "client_id": body["client_id"], "refresh_token": credential.refresh,
            })
        except AuthError:
            if attempt + 1 == attempts:
                raise
        else:
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt + 1 == attempts:
                return token(response_json(response), credential)
        await asyncio.sleep(2 ** attempt)
    raise AuthError("OAuth refresh failed")
