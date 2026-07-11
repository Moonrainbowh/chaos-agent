from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10.
    import tomli as tomllib

from code_agent.policy.models import ApprovalMode
from code_agent.providers.config import ApiProtocol, ConfiguredApiKey, ProviderConfig
from code_agent.providers.errors import ProviderConfigError


class LocalConfigError(ValueError):
    """A diagnostic-safe local configuration error."""


@dataclass(frozen=True)
class RuntimeConfig:
    provider: ProviderConfig
    profile: str
    approval_mode: ApprovalMode
    allow_sensitive_paths: bool
    config_path: Path

    @property
    def key_status(self) -> str:
        return self.provider.key_status


def default_config_path(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    base = source.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "code-agent" / "config.toml"


def resolve_config_path(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    value = source.get("CODE_AGENT_CONFIG")
    if value is None:
        return default_config_path(source)
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise LocalConfigError("CODE_AGENT_CONFIG must be an absolute path")
    return path


def load_runtime_config(
    *, env: Mapping[str, str] | None = None, cli_profile: str | None = None
) -> RuntimeConfig:
    source = os.environ if env is None else env
    path = resolve_config_path(source)
    document = _read_document(path)
    selected, values = _select_provider(document, source, cli_profile)
    provider = _provider_config(values, source)
    return RuntimeConfig(
        provider=provider,
        profile=selected,
        approval_mode=_approval_mode(document, source),
        allow_sensitive_paths=_allow_sensitive_paths(document, source),
        config_path=path,
    )


def _read_document(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise LocalConfigError(f"configuration path is not a file: {path}")
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise LocalConfigError(f"invalid configuration at {path}: {type(error).__name__}") from None
    if not isinstance(data, dict):
        raise LocalConfigError(f"invalid configuration at {path}: document")
    return data


def _select_provider(
    document: Mapping[str, Any], env: Mapping[str, str], cli_profile: str | None
) -> tuple[str, Mapping[str, Any]]:
    if not document:
        return "environment", {}
    default = _table(document, "default")
    providers = _table(document, "providers")
    configured = default.get("provider")
    selected = cli_profile or env.get("CODE_AGENT_PROFILE") or configured
    if not isinstance(selected, str) or not selected.strip():
        raise LocalConfigError("default.provider must name a provider")
    values = providers.get(selected)
    if not isinstance(values, dict):
        raise LocalConfigError(f"unknown provider profile: {selected}")
    return selected, values


def _provider_config(values: Mapping[str, Any], env: Mapping[str, str]) -> ProviderConfig:
    api = _protocol(env.get("CODE_AGENT_API", values.get("api", "responses")))
    base_url = _text(env.get("CODE_AGENT_BASE_URL", values.get("base_url", "https://api.openai.com")), "base_url")
    model = _text(env.get("CODE_AGENT_MODEL", values.get("model", "gpt-4.1-mini")), "model")
    override_key_env = env.get("CODE_AGENT_API_KEY_ENV")
    configured_key = values.get("api_key")
    profile_key_env = values.get("api_key_env")
    if override_key_env is not None:
        return ProviderConfig(base_url, model, api, _text(override_key_env, "api_key_env"))
    if (configured_key is None) == (profile_key_env is None) and values:
        raise LocalConfigError("provider must define exactly one of api_key or api_key_env")
    try:
        if configured_key is not None:
            return ProviderConfig(base_url, model, api, api_key_source=ConfiguredApiKey(_text(configured_key, "api_key")))
        key_env = profile_key_env or env.get("CODE_AGENT_API_KEY_ENV", "OPENAI_API_KEY")
        return ProviderConfig(base_url, model, api, _text(key_env, "api_key_env"))
    except ProviderConfigError as error:
        raise LocalConfigError(f"invalid provider configuration: {error}") from None


def _approval_mode(document: Mapping[str, Any], env: Mapping[str, str]) -> ApprovalMode:
    agent = document.get("agent", {})
    if agent is not None and not isinstance(agent, dict):
        raise LocalConfigError("agent must be a table")
    value = env.get("CODE_AGENT_APPROVAL_MODE", agent.get("approval_mode", "ask"))
    try:
        return ApprovalMode(_text(value, "approval_mode"))
    except ValueError:
        raise LocalConfigError(
            "approval_mode must be plan, ask, auto, elevated, or full-local"
        ) from None


def _allow_sensitive_paths(document: Mapping[str, Any], env: Mapping[str, str]) -> bool:
    agent = document.get("agent", {})
    if agent is not None and not isinstance(agent, dict):
        raise LocalConfigError("agent must be a table")
    value = env.get("CODE_AGENT_ALLOW_SENSITIVE_PATHS", agent.get("allow_sensitive_paths", False))
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.casefold() in {"1", "true", "yes"}:
        return True
    if isinstance(value, str) and value.casefold() in {"0", "false", "no"}:
        return False
    raise LocalConfigError("allow_sensitive_paths must be a boolean")


def _table(document: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = document.get(name)
    if not isinstance(value, dict):
        raise LocalConfigError(f"{name} must be a table")
    return value


def _protocol(value: object) -> ApiProtocol:
    try:
        return ApiProtocol(_text(value, "api"))
    except ValueError:
        raise LocalConfigError("api must be responses, chat_completions, or anthropic_messages") from None


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LocalConfigError(f"{field} must be non-empty text")
    return value
