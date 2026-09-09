"""WorkBuddy China browser session protocol ported from uri-agent (MIT)."""
from __future__ import annotations

import asyncio
import time
import uuid
from urllib.parse import urlencode, urlsplit

from .flows_common import request, required, response_json, safe_url, show
from .models import AuthError, Credential

ENDPOINT = "https://copilot.tencent.com"
USER_AGENT = "WorkBuddy/5.5.3 WorkBuddy/5.5.3 CLI/2.137.1"


def headers(endpoint, access=None, domain=None):
    value = {"Accept": "application/json", "User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest",
             "X-Product": "SaaS", "X-Domain": domain or urlsplit(endpoint).netloc}
    if access:
        value["Authorization"] = "Bearer " + access
    else:
        value["X-No-Authorization"] = "true"
    value.update({"X-No-User-Id": "true", "X-No-Enterprise-Id": "true", "X-No-Department-Info": "true"})
    return value


def payload(response, pending=()):
    value = response_json(response, check=False)
    try:
        code = int(value.get("code", 0))
    except (ValueError, TypeError):
        raise AuthError("Invalid WorkBuddy response code") from None
    if code in pending:
        return None
    if not response.is_success or code != 0:
        raise AuthError(f"WorkBuddy request failed (HTTP {response.status_code}, code {code})")
    data = value.get("data", value)
    if not isinstance(data, dict):
        raise AuthError("Invalid WorkBuddy response payload")
    return data


async def poll_stage(client, endpoint, path, request_headers, key, pending):
    auth_retries = 0
    while True:
        await asyncio.sleep(1)
        response = await request(client, "GET", endpoint + path, headers=request_headers)
        if key == "uid" and response.status_code in {401, 403} and auth_retries < 5:
            auth_retries += 1
            continue
        value = payload(response, pending)
        if value is not None and value.get(key):
            return value


async def accounts(client, endpoint, auth, *, required_accounts):
    try:
        value = payload(await request(client, "GET", endpoint + "/v2/plugin/accounts",
                                      headers=headers(endpoint, required(auth, "accessToken"), auth.get("domain"))))
        result = value.get("accounts", [])
        if not isinstance(result, list) or (required_accounts and not result):
            raise AuthError("WorkBuddy account list is empty or invalid")
        return result
    except AuthError:
        if required_accounts:
            raise
        return []


def session(auth, endpoint, current, all_accounts, environment="internal"):
    merged = dict(current)
    for candidate in all_accounts:
        if isinstance(candidate, dict) and all(candidate.get(k) == current.get(k) for k in ("uid", "enterpriseId")):
            merged.update(candidate)
            break
    refresh = auth.get("refreshToken") or ""
    expires = None
    if refresh:
        try:
            expires = float(auth.get("expiresAt") or (time.time() + float(auth.get("expiresIn", 86400))))
            if expires >= 10_000_000_000:
                expires /= 1000
            expires -= 300
        except (TypeError, ValueError):
            raise AuthError("Invalid WorkBuddy expiry") from None
    extra = {"workbuddyEndpoint": endpoint, "workbuddyEnvironment": environment,
             "workbuddyDomain": auth.get("domain") or urlsplit(endpoint).netloc,
             "workbuddyAccount": merged, "workbuddyAccounts": all_accounts,
             "workbuddyAuth": {k: v for k, v in auth.items() if k not in {"accessToken", "refreshToken"}}}
    if auth.get("method"):
        extra["workbuddyAuthMethod"] = auth["method"]
    return Credential("oauth", required(auth, "accessToken"), refresh, expires, extra)


async def login_workbuddy(client, display):
    setup = payload(await request(client, "POST", ENDPOINT + "/v2/plugin/auth/state?platform=workbuddy",
                                  headers=headers(ENDPOINT), json={}))
    state = required(setup, "state")
    url = safe_url(required(setup, "authUrl"))
    url += ("&" if "?" in url else "?") + urlencode({"version": "5.5.3", "loginSessionId": str(uuid.uuid4())})
    await show(url, display)
    query = "?" + urlencode({"state": state})
    auth = await poll_stage(client, ENDPOINT, "/v2/plugin/auth/token" + query, headers(ENDPOINT), "accessToken", (11217,))
    current = await poll_stage(client, ENDPOINT, "/v2/plugin/login/account" + query,
                               headers(ENDPOINT, required(auth, "accessToken"), auth.get("domain")), "uid", (12151,))
    all_accounts = await accounts(client, ENDPOINT, auth, required_accounts=False)
    return session(auth, ENDPOINT, current, all_accounts)


async def refresh_workbuddy(client, credential):
    endpoint = safe_url(str(credential.extra.get("workbuddyEndpoint", ENDPOINT))).rstrip("/").removesuffix("/v2")
    current = credential.extra.get("workbuddyAccount")
    if not isinstance(current, dict):
        raise AuthError("WorkBuddy current account missing; sign in again")
    domain = credential.extra.get("workbuddyDomain") or urlsplit(endpoint).netloc
    request_headers = headers(endpoint, credential.access, domain)
    request_headers.update({"X-Refresh-Token": credential.refresh, "X-Auth-Refresh-Source": "plugin"})
    auth = payload(await request(client, "POST", endpoint + "/v2/plugin/auth/token/refresh", headers=request_headers, json={}))
    auth.setdefault("domain", domain)
    auth.setdefault("refreshToken", credential.refresh)
    all_accounts = await accounts(client, endpoint, auth, required_accounts=True)
    return session(auth, endpoint, current, all_accounts, credential.extra.get("workbuddyEnvironment", "internal"))
