"""Account-bound WorkBuddy cloud model discovery, ported from URI Agent (MIT)."""
from __future__ import annotations

import base64
import json
from urllib.parse import urlsplit

import httpx

from .catalog import CatalogModel
from .flows_workbuddy import ENDPOINT, headers
from .models import AuthError, Credential


def _endpoint(credential: Credential) -> str:
    value = credential.extra.get("workbuddyEndpoint", ENDPOINT)
    if not isinstance(value, str):
        raise AuthError("Invalid WorkBuddy session endpoint")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or any(ord(c) < 32 for c in value)):
            raise ValueError
        _ = parsed.port
    except ValueError:
        raise AuthError("Invalid WorkBuddy session endpoint") from None
    return value.rstrip("/").removesuffix("/v2")


def request_headers(credential: Credential, endpoint: str) -> httpx.Headers:
    """Use the selected account's identity, never the login bootstrap no-user flags."""
    extra = credential.extra
    values = headers(endpoint, credential.access, extra.get("workbuddyDomain"))
    values = {k: v for k, v in values.items() if not k.lower().startswith("x-no-")}
    values["Connection"] = "close"
    if credential.kind == "api_key":
        values["X-Api-Key"] = credential.access
    account = extra.get("workbuddyAccount", {})
    if not isinstance(account, dict):
        raise AuthError("Invalid WorkBuddy account metadata")
    fields = {"uid": "X-User-Id", "enterpriseId": "X-Enterprise-Id",
              "departmentFullName": "X-Department-Info", "idSource": "X-Id-Source"}
    for key, header in fields.items():
        if isinstance(account.get(key), str) and account[key]:
            values[header] = account[key]
    if "X-Enterprise-Id" in values:
        values["X-Tenant-Id"] = values["X-Enterprise-Id"]
    method = extra.get("workbuddyAuthMethod")
    if isinstance(method, str) and method:
        values["X-Auth-Method"] = method
    userinfo = {target: values[source] for source, target in {
        "X-User-Id": "uin", "X-Enterprise-Id": "owner_uin",
        "X-Id-Source": "id_source", "X-Auth-Method": "token_source"}.items() if source in values}
    if "uin" in userinfo and len(userinfo) > 1:
        values["X-Userinfo"] = base64.b64encode(json.dumps(userinfo, separators=(",", ":")).encode()).decode()
    if any(not isinstance(v, str) or any(ord(c) < 32 or ord(c) == 127 for c in v) for v in values.values()):
        raise AuthError("Invalid WorkBuddy request metadata")
    return httpx.Headers({k: v.encode("utf-8") for k, v in values.items()})


async def discover(credential: Credential, client: httpx.AsyncClient | None = None) -> tuple[CatalogModel, ...]:
    """Fetch /v3/config in five seconds; never follow redirects or persist auth data."""
    endpoint = _endpoint(credential)
    if client is None:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as owned:
            return await discover(credential, owned)
    try:
        response = await client.get(endpoint + "/v3/config", headers=request_headers(credential, endpoint),
                                    timeout=5, follow_redirects=False)
        if not response.is_success:
            raise AuthError(f"WorkBuddy model discovery failed (HTTP {response.status_code})")
        value = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        raise AuthError("WorkBuddy model discovery failed; sign-in was preserved") from None
    return parse_product_config(value, endpoint)


def parse_product_config(value: object, endpoint: str) -> tuple[CatalogModel, ...]:
    """Map cloud tool-capable chat models and explicit token/image limits."""
    if not isinstance(value, dict):
        raise AuthError("Invalid WorkBuddy cloud configuration")
    code = value.get("code", 0)
    if code not in (0, "0"):
        raise AuthError("WorkBuddy cloud configuration was rejected")
    product = value.get("data", value)
    if isinstance(product, dict):
        product = product.get("data", product)
    records = product.get("models") if isinstance(product, dict) else None
    if not isinstance(records, list):
        raise AuthError("WorkBuddy cloud configuration has no models array")
    models = tuple(model for record in records if (model := _model(record, endpoint)) is not None)
    if not models:
        raise AuthError("WorkBuddy cloud configuration has no runnable chat models")
    return models


def _model(raw: object, endpoint: str) -> CatalogModel | None:
    if not isinstance(raw, dict) or raw.get("supportsToolCall") is not True:
        return None
    identity = raw.get("id")
    if not isinstance(identity, str) or not identity.strip():
        return None
    context, output = raw.get("maxInputTokens"), raw.get("maxOutputTokens")
    if any(type(value) is not int or value <= 0 for value in (context, output)):
        return None
    name = raw.get("name")
    return CatalogModel(identity.strip(), name if isinstance(name, str) and name.strip() else identity.strip(),
                        "workbuddy", "chat_completions", endpoint.rstrip("/") + "/v2", "/chat/completions",
                        context, output, ("text", "image") if raw.get("supportsImages") is True else ("text",))
