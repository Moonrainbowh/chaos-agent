from __future__ import annotations

from typing import Any, Mapping

from code_agent.authentication.models import AuthError
from code_agent.authentication.source import StoredCredentialSource
from code_agent.authentication.store import default_auth_path, validate_provider_id
from code_agent.providers.errors import ProviderConfigError


def provider_auth_options(values: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, object]:
    """Resolve credential references without reading tokens or logging in."""
    result: dict[str, object] = {}
    provider_id = values.get("provider_id")
    if provider_id is not None:
        try:
            validate_provider_id(provider_id)
        except AuthError as error:
            raise ProviderConfigError(str(error)) from None
        result["provider_id"] = provider_id
    auth = values.get("auth")
    if auth is not None:
        if auth not in {"oauth", "api_key"} or provider_id is None:
            raise ProviderConfigError("auth requires oauth/api_key and a provider_id")
        if values.get("api_key") is not None or values.get("api_key_env") is not None:
            raise ProviderConfigError("auth cannot be combined with another credential source")
        from code_agent.authentication.registry import get_provider
        try:
            platform = get_provider(provider_id)
            if auth == "oauth" and not platform.oauth_methods:
                raise ProviderConfigError("This provider does not support account login")
            if auth == "api_key" and not platform.offers_api_key:
                raise ProviderConfigError("This provider requires OAuth")
            result["auth_source"] = StoredCredentialSource(provider_id, default_auth_path(env), auth)
        except (AuthError, KeyError, ValueError) as error:
            raise ProviderConfigError("Invalid stored credential reference") from None
    for field in ("responses_path", "chat_completions_path", "anthropic_messages_path", "pi_messages_path"):
        if field in values:
            result[field] = values[field]
    return result
