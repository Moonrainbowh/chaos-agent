"""Provider wire compatibility, adapted from github.com/4fuu/uri-agent (MIT)."""
from __future__ import annotations

import base64
import json
import uuid
from urllib.parse import quote, urlsplit

from .errors import ProviderConfigError


_OAUTH_HOSTS = {
    "anthropic": {"api.anthropic.com"},
    "openai-codex": {"chatgpt.com"},
    "github-copilot": {"api.githubcopilot.com", "api.individual.githubcopilot.com", "api.business.githubcopilot.com", "api.enterprise.githubcopilot.com"},
    "kimi-coding": {"api.kimi.com"},
    "openrouter": {"openrouter.ai"},
    "xai": {"api.x.ai"},
    "muse-code": {"api.meta.ai"},
    "antigravity": {"cloudcode-pa.googleapis.com", "daily-cloudcode-pa.googleapis.com", "daily-cloudcode-pa.sandbox.googleapis.com"},
}


def authenticated_request(config, credential, path, payload, headers):
    """Return a credential-bound URL, copied body and case-normalized headers."""
    provider = config.provider_id
    if provider in {"openai-codex", "antigravity"} and credential.kind != "oauth":
        raise ProviderConfigError(f"{provider} requires OAuth login")
    extra = credential.extra
    base = config.base_url
    if provider in {"cloudflare-ai-gateway", "cloudflare-workers-ai"}:
        base = _cloudflare_base(provider, extra)
    if credential.kind == "oauth":
        base = _oauth_base(provider, base, extra)
        path = _oauth_path(provider, config.api.value)
    body = dict(payload)
    result = {key.lower(): value for key, value in headers.items()}
    if provider == "muse-code":
        result["x-api-version"] = "1.0.0"
    if credential.kind == "oauth":
        result.pop("x-api-key", None)
        result.pop("x-goog-api-key", None)
        result["authorization"] = f"Bearer {credential.access}"
    if provider == "openai-codex":
        body.update(store=False, include=["reasoning.encrypted_content"],
                    tool_choice="auto", parallel_tool_calls=True, text={"verbosity": "low"})
        body.pop("max_output_tokens", None)
        result.update({"openai-beta": "responses=experimental", "version": "0.153.0"})
        if extra.get("accountId"):
            result["chatgpt-account-id"] = extra["accountId"]
    if config.api.value == "anthropic_messages" and credential.kind == "oauth":
        result.update({"x-app": "cli", "user-agent": "claude-cli/2.1.251",
                       "anthropic-beta": "claude-code-20250219,oauth-2025-04-20"})
    if provider == "github-copilot":
        _copilot_headers(result, body)
    if provider == "workbuddy":
        if "max_completion_tokens" in body:
            body["max_tokens"] = body.pop("max_completion_tokens")
        base = base.rstrip("/")
        if not base.endswith("/v2"):
            base += "/v2"
        _workbuddy_headers(result, credential)
    if provider == "antigravity":
        path = "/v1internal:streamGenerateContent?alt=sse"
        body = _antigravity_body(body, config.model, extra)
        result["user-agent"] = "antigravity/4.3.0"
    return f"{base}{path}", body, result


def _cloudflare_base(provider, extra):
    account = extra.get("accountId")
    if not isinstance(account, str) or not account:
        raise ProviderConfigError("Cloudflare credential has no accountId")
    if provider == "cloudflare-ai-gateway":
        gateway = extra.get("gatewayId")
        if not isinstance(gateway, str) or not gateway:
            raise ProviderConfigError("Cloudflare credential has no gatewayId")
        return f"https://gateway.ai.cloudflare.com/v1/{quote(account, safe='')}/{quote(gateway, safe='')}/compat"
    return f"https://api.cloudflare.com/client/v4/accounts/{quote(account, safe='')}/ai/v1"


def _oauth_base(provider, base, extra):
    if provider in {"radius", "workbuddy"}:
        key = "gateway" if provider == "radius" else "workbuddyEndpoint"
        bound = extra.get(key)
        if not isinstance(bound, str):
            raise ProviderConfigError(f"{provider} login has no bound endpoint")
        parsed = urlsplit(bound)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ProviderConfigError("OAuth endpoint must be a clean HTTPS URL")
        if provider == "radius":
            configured = urlsplit(base)
            if (configured.scheme, configured.hostname, configured.port or 443) != (parsed.scheme, parsed.hostname, parsed.port or 443):
                raise ProviderConfigError("Radius model endpoint and login gateway must have the same origin")
            if configured.username or configured.password or configured.query or configured.fragment:
                raise ProviderConfigError("Radius model endpoint must be a clean HTTPS URL")
            return base.rstrip("/")
        return bound.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or parsed.port not in {None, 443} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.hostname not in _OAUTH_HOSTS.get(provider, set()):
        raise ProviderConfigError("OAuth model endpoint does not belong to the logged-in provider")
    prefixes = {"anthropic": "", "openai-codex": "/backend-api", "github-copilot": "",
                "kimi-coding": "/coding", "openrouter": "/api/v1", "xai": "/v1",
                "muse-code": "/v1", "antigravity": ""}
    return f"https://{parsed.hostname}{prefixes[provider]}"


def _oauth_path(provider, protocol):
    if provider == "openai-codex":
        return "/codex/responses"
    if provider == "radius":
        return "/messages"
    paths = {"responses": "/responses", "chat_completions": "/chat/completions",
             "anthropic_messages": "/v1/messages", "google_generative_ai": "/v1internal:streamGenerateContent?alt=sse"}
    if protocol not in paths:
        raise ProviderConfigError("OAuth provider protocol is unsupported")
    return paths[protocol]


def _copilot_headers(headers, body):
    history = body.get("messages", body.get("input", []))
    last = history[-1] if isinstance(history, list) and history else {}
    initiator = "agent" if last.get("role") in {"assistant", "tool"} or last.get("type") == "function_call_output" else "user"
    headers.update({"user-agent": "copilot/1.0.82", "editor-version": "copilot/1.0.82",
                    "copilot-integration-id": "copilot-developer-cli", "copilot-harness-id": "copilot-sdk",
                    "openai-intent": "conversation-agent", "x-github-api-version": "2026-08-01",
                    "x-initiator": initiator, "x-interaction-type": f"conversation-{initiator}"})


def _workbuddy_headers(headers, credential):
    extra = credential.extra
    headers.update({"authorization": f"Bearer {credential.access}",
                    "x-requested-with": "XMLHttpRequest", "x-product": "SaaS",
                    "user-agent": "WorkBuddy/5.5.3 WorkBuddy/5.5.3 CLI/2.137.1"})
    if credential.kind == "api_key":
        headers["x-api-key"] = credential.access
    account = extra.get("workbuddyAccount", {})
    fields = {"uid": "x-user-id", "enterpriseId": "x-enterprise-id",
              "departmentFullName": "x-department-info", "idSource": "x-id-source"}
    for source, target in fields.items():
        if account.get(source):
            headers[target] = str(account[source])
    for source, target in {"workbuddyDomain": "x-domain", "workbuddyAuthMethod": "x-auth-method"}.items():
        if extra.get(source):
            headers[target] = str(extra[source])
    if account.get("enterpriseId"):
        headers["x-tenant-id"] = str(account["enterpriseId"])
    info = {target: headers[source] for source, target in {
        "x-user-id": "uin", "x-enterprise-id": "owner_uin",
        "x-id-source": "id_source", "x-auth-method": "token_source"}.items() if source in headers}
    if "uin" in info and len(info) > 1:
        headers["x-userinfo"] = base64.b64encode(json.dumps(info).encode()).decode()


def _antigravity_body(body, model, extra):
    project = extra.get("projectId")
    if not isinstance(project, str) or not project:
        raise ProviderConfigError("Antigravity login has no projectId")
    for content in body.get("contents", []):
        for part in content.get("parts", []):
            if "functionCall" in part:
                part.setdefault("thoughtSignature", "skip_thought_signature_validator")
    return {"project": project, "model": model, "request": body,
            "requestId": f"agent/{uuid.uuid4()}", "userAgent": "antigravity",
            "requestType": "agent", "enabledCreditTypes": ["GOOGLE_ONE_AI"]}
