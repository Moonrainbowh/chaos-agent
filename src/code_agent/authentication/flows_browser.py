"""Browser PKCE protocol mappings from uri-agent src/oauth/providers (MIT)."""
from __future__ import annotations

import secrets
from urllib.parse import urlencode, urlsplit

from .callback import BrowserCallback
from .flows_common import account_id, pkce, request, required, response_json, safe_url, token, with_extra
from .models import AuthError, Credential

CODEX_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ANTHROPIC_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CODEX_TOKEN = "https://auth.openai.com/oauth/token"
ANTHROPIC_TOKEN = "https://platform.claude.com/v1/oauth/token"


def gateway(options):
    """Normalize a Radius issuer to its origin, preserving scheme and port."""
    value = options.get("gateway", "https://radius.pi.dev")
    if not isinstance(value, str):
        raise AuthError("Invalid Radius gateway")
    value = value.strip() or "https://radius.pi.dev"
    value = safe_url(value if "://" in value else "https://" + value)
    parts = urlsplit(value)
    if "?" in value:
        raise AuthError("Radius gateway must not contain a query")
    return f"{parts.scheme}://{parts.netloc}"


async def exchange_codex(client, code, verifier, redirect):
    response = await request(client, "POST", CODEX_TOKEN, data={
        "grant_type": "authorization_code", "client_id": CODEX_ID,
        "code": code, "code_verifier": verifier, "redirect_uri": redirect,
    })
    credential = token(response_json(response))
    return with_extra(credential, accountId=account_id(credential.access))


async def login_browser(provider, client, options, display, read_input):
    verifier, challenge = pkce()
    state = verifier if provider == "anthropic" else secrets.token_urlsafe(24)
    if provider == "openrouter":
        return await openrouter(client, verifier, challenge, display, read_input)
    authorize, redirect, fields = await browser_config(provider, client, options)
    fields.update(code_challenge=challenge, code_challenge_method="S256", state=state)
    async with BrowserCallback(redirect, state) as callback:
        url = authorize + ("&" if "?" in authorize else "?") + urlencode(fields)
        code = await callback.receive(url, display, read_input)
    if provider == "openai-codex":
        return await exchange_codex(client, code, verifier, redirect)
    body = {"grant_type": "authorization_code", "client_id": fields["client_id"],
            "code": code, "code_verifier": verifier, "redirect_uri": redirect}
    if provider == "anthropic":
        response = await request(client, "POST", ANTHROPIC_TOKEN, json={**body, "state": state})
        return token(response_json(response))
    base = gateway(options)
    response = await request(client, "POST", base + "/v1/oauth/token", data=body)
    return with_extra(token(response_json(response)), gateway=base)


async def browser_config(provider, client, options):
    if provider == "openai-codex":
        redirect = "http://localhost:1455/auth/callback"
        return "https://auth.openai.com/oauth/authorize", redirect, {
            "response_type": "code", "client_id": CODEX_ID, "redirect_uri": redirect,
            "scope": "openid profile email offline_access", "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true", "originator": "pi",
        }
    if provider == "anthropic":
        redirect = "http://localhost:53692/callback"
        return "https://claude.ai/oauth/authorize", redirect, {
            "code": "true", "response_type": "code", "client_id": ANTHROPIC_ID,
            "redirect_uri": redirect,
            "scope": "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload",
        }
    config = response_json(await request(client, "GET", gateway(options) + "/v1/oauth"))
    redirect = "http://127.0.0.1:1456/oauth/callback"
    return safe_url(required(config, "authorizationEndpoint")), redirect, {
        "response_type": "code", "client_id": "pi-gateway", "redirect_uri": redirect,
        "scope": "gateway offline_access", "handoff": "url",
    }


async def openrouter(client, verifier, challenge, display, read_input):
    path = "/oauth/callback/" + secrets.token_hex(16)
    async with BrowserCallback("http://127.0.0.1:0" + path, None) as callback:
        if callback.server is None:
            from .models import AuthError
            raise AuthError("Cannot bind OpenRouter callback listener")
        fields = {"callback_url": callback.redirect, "code_challenge": challenge,
                  "code_challenge_method": "S256"}
        code = await callback.receive("https://openrouter.ai/auth?" + urlencode(fields), display, read_input)
    value = response_json(await request(client, "POST", "https://openrouter.ai/api/v1/auth/keys", json={
        "code": code, "code_verifier": verifier, "code_challenge_method": "S256",
    }))
    return Credential("oauth", required(value, "key"))


async def refresh_browser(provider, credential, client):
    body = {"grant_type": "refresh_token", "refresh_token": credential.refresh}
    if provider == "anthropic":
        body["client_id"] = ANTHROPIC_ID
        response = await request(client, "POST", ANTHROPIC_TOKEN, json=body)
    elif provider == "openai-codex":
        body["client_id"] = CODEX_ID
        response = await request(client, "POST", CODEX_TOKEN, data=body)
    else:
        body["client_id"] = "pi-gateway"
        response = await request(client, "POST", gateway(credential.extra) + "/v1/oauth/token", data=body)
    updated = token(response_json(response), credential)
    if provider == "openai-codex":
        updated = with_extra(updated, accountId=account_id(updated.access))
    elif provider == "radius":
        updated = with_extra(updated, gateway=gateway(credential.extra))
    return updated
