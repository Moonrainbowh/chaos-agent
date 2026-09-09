from __future__ import annotations
from typing import Any, Mapping
from code_agent.config._environment import environment_value as _environment_value
from code_agent.providers.config import ProviderConfig, ConfiguredApiKey
from code_agent.providers.errors import ProviderConfigError


def provider_config(values: Mapping[str, Any], env: Mapping[str, str], *, allow_environment: bool) -> ProviderConfig:
    from .loader import LocalConfigError, _protocol, _text
    from .provider_auth import provider_auth_options
    override = lambda primary, legacy, default=None: _environment_value(env, primary, legacy, default) if allow_environment else default
    api = _protocol(override("CHAOS_API", "CODE_AGENT_API", values.get("api")))
    base_url = _text(override("CHAOS_BASE_URL", "CODE_AGENT_BASE_URL", values.get("base_url")), "base_url")
    model = _text(override("CHAOS_MODEL", "CODE_AGENT_MODEL", values.get("model")), "model")
    override_key_env = override("CHAOS_API_KEY_ENV", "CODE_AGENT_API_KEY_ENV")
    configured_key = values.get("api_key")
    profile_key_env = values.get("api_key_env")
    try:
        options = provider_auth_options(values, env)
    except ProviderConfigError as error:
        raise LocalConfigError(str(error)) from None
    if "auth_source" in options:
        if override_key_env is not None:
            raise LocalConfigError("API key override cannot replace stored authentication")
        return ProviderConfig(base_url, model, api, **options)
    if override_key_env is not None:
        return ProviderConfig(base_url, model, api, _text(override_key_env, "api_key_env"), **options)
    if (configured_key is None) == (profile_key_env is None) and values:
        raise LocalConfigError("provider must define exactly one of api_key or api_key_env")
    try:
        if configured_key is not None:
            return ProviderConfig(base_url, model, api, api_key_source=ConfiguredApiKey(_text(configured_key, "api_key")), **options)
        key_env = profile_key_env or override("CHAOS_API_KEY_ENV", "CODE_AGENT_API_KEY_ENV", "OPENAI_API_KEY")
        return ProviderConfig(base_url, model, api, _text(key_env, "api_key_env"), **options)
    except ProviderConfigError as error:
        raise LocalConfigError(f"invalid provider configuration: {error}") from None

