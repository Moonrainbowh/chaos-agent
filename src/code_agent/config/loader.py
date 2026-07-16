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
from code_agent.providers.config import ApiProtocol, ConfiguredApiKey, ModelProfile, ProviderConfig
from code_agent.providers.errors import ProviderConfigError
from code_agent.mcp.registry import McpRisk, McpServer


class LocalConfigError(ValueError):
    """A diagnostic-safe local configuration error."""


@dataclass(frozen=True)
class RuntimeConfig:
    provider: ProviderConfig
    profile: str
    approval_mode: ApprovalMode
    allow_sensitive_paths: bool
    config_path: Path
    profiles: tuple[ModelProfile, ...] = ()
    mcp_servers: tuple[McpServer, ...] = ()

    @property
    def key_status(self) -> str:
        return self.provider.key_status


def default_config_path(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    base = source.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "chaos-agent" / "config.toml"


def resolve_config_path(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    value = _environment_value(source, "CHAOS_CONFIG", "CODE_AGENT_CONFIG")
    if value is None:
        default = default_config_path(source)
        legacy = _legacy_config_path(source)
        return legacy if not default.exists() and legacy.exists() else default
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise LocalConfigError("CHAOS_CONFIG must be an absolute path")
    return path


def load_runtime_config(
    *, env: Mapping[str, str] | None = None, cli_profile: str | None = None
) -> RuntimeConfig:
    source = os.environ if env is None else env
    path = resolve_config_path(source)
    document = _read_document(path)
    selected, values = _select_provider(document, source, cli_profile)
    provider = _provider_config(values, source, allow_environment=True)
    return RuntimeConfig(
        provider=provider,
        profile=selected,
        approval_mode=_approval_mode(document, source),
        allow_sensitive_paths=_allow_sensitive_paths(document, source),
        config_path=path, profiles=_profiles(document, source, selected, provider), mcp_servers=_mcp_servers(document),
    )


def _profiles(document: Mapping[str, Any], env: Mapping[str, str], selected: str, provider: ProviderConfig) -> tuple[ModelProfile, ...]:
    if not document:
        return (ModelProfile("environment", provider, 128_000, 16_384),)
    configured = _table(document, "providers")
    values: list[ModelProfile] = []
    for name, raw in configured.items():
        if not isinstance(name, str) or not isinstance(raw, dict): raise LocalConfigError("providers must map names to tables")
        current = provider if name == selected else _provider_config(raw, env, allow_environment=False)
        values.append(ModelProfile(name, current, _required_positive(raw, "context_window"), _required_positive(raw, "max_output_tokens"), _positive(raw.get("max_agent_rounds", 50), "max_agent_rounds"), _positive(raw.get("max_tool_calls", 128), "max_tool_calls"), _positive(raw.get("max_tool_calls_per_round", 50), "max_tool_calls_per_round")))
    return tuple(values)


def _positive(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0: raise LocalConfigError(f"{name} must be a positive integer")
    return value


def _required_positive(values: Mapping[str, Any], name: str) -> int:
    if name not in values:
        raise LocalConfigError(f"provider must define {name}")
    return _positive(values[name], name)


def _mcp_servers(document: Mapping[str, Any]) -> tuple[McpServer, ...]:
    mcp = document.get("mcp", {})
    if not isinstance(mcp, dict): raise LocalConfigError("mcp must be a table")
    servers = mcp.get("servers", {})
    if not isinstance(servers, dict): raise LocalConfigError("mcp.servers must be a table")
    try:
        items: list[McpServer] = []
        for name, raw in servers.items():
            if not isinstance(name, str) or not isinstance(raw, dict): raise LocalConfigError("mcp.servers must map names to tables")
            enabled, approved = raw.get("enabled", False), raw.get("approved", False)
            if not isinstance(enabled, bool) or not isinstance(approved, bool): raise LocalConfigError("MCP enabled and approved must be boolean")
            command = _text(raw.get("command"), "mcp command")
            args = raw.get("args", ())
            environment = raw.get("environment", ())
            if not isinstance(args, list) or not all(isinstance(item, str) and item for item in args): raise LocalConfigError("mcp args must be text array")
            if not isinstance(environment, list) or not all(isinstance(item, str) and item for item in environment): raise LocalConfigError("mcp environment must be text array")
            cwd = raw.get("cwd")
            if cwd is not None and not isinstance(cwd, str): raise LocalConfigError("mcp cwd must be text")
            raw_risks = raw.get("tool_risks", {})
            if not isinstance(raw_risks, dict): raise LocalConfigError("mcp tool_risks must be a table")
            try: risks = {tool: McpRisk(risk) for tool, risk in raw_risks.items() if isinstance(tool, str) and isinstance(risk, str)}
            except ValueError: raise LocalConfigError("mcp tool risk must be read, write, network, or critical") from None
            if len(risks) != len(raw_risks): raise LocalConfigError("mcp tool risks must map text names")
            items.append(McpServer(name, command, tuple(args), cwd, tuple(environment), enabled, approved, (), risks))
        return tuple(items)
    except ValueError as error: raise LocalConfigError(f"invalid MCP configuration: {error}") from None


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
    selected = cli_profile or _environment_value(env, "CHAOS_PROFILE", "CODE_AGENT_PROFILE") or configured
    if not isinstance(selected, str) or not selected.strip():
        raise LocalConfigError("default.provider must name a provider")
    values = providers.get(selected)
    if not isinstance(values, dict):
        raise LocalConfigError(f"unknown provider profile: {selected}")
    return selected, values


def _provider_config(values: Mapping[str, Any], env: Mapping[str, str], *, allow_environment: bool) -> ProviderConfig:
    override = lambda primary, legacy, default=None: _environment_value(env, primary, legacy, default) if allow_environment else default
    api = _protocol(override("CHAOS_API", "CODE_AGENT_API", values.get("api")))
    base_url = _text(override("CHAOS_BASE_URL", "CODE_AGENT_BASE_URL", values.get("base_url")), "base_url")
    model = _text(override("CHAOS_MODEL", "CODE_AGENT_MODEL", values.get("model")), "model")
    override_key_env = override("CHAOS_API_KEY_ENV", "CODE_AGENT_API_KEY_ENV")
    configured_key = values.get("api_key")
    profile_key_env = values.get("api_key_env")
    if override_key_env is not None:
        return ProviderConfig(base_url, model, api, _text(override_key_env, "api_key_env"))
    if (configured_key is None) == (profile_key_env is None) and values:
        raise LocalConfigError("provider must define exactly one of api_key or api_key_env")
    try:
        if configured_key is not None:
            return ProviderConfig(base_url, model, api, api_key_source=ConfiguredApiKey(_text(configured_key, "api_key")))
        key_env = profile_key_env or override("CHAOS_API_KEY_ENV", "CODE_AGENT_API_KEY_ENV", "OPENAI_API_KEY")
        return ProviderConfig(base_url, model, api, _text(key_env, "api_key_env"))
    except ProviderConfigError as error:
        raise LocalConfigError(f"invalid provider configuration: {error}") from None


def _approval_mode(document: Mapping[str, Any], env: Mapping[str, str]) -> ApprovalMode:
    agent = document.get("agent", {})
    if agent is not None and not isinstance(agent, dict):
        raise LocalConfigError("agent must be a table")
    value = _environment_value(env, "CHAOS_APPROVAL_MODE", "CODE_AGENT_APPROVAL_MODE", agent.get("approval_mode", "ask"))
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
    value = _environment_value(env, "CHAOS_ALLOW_SENSITIVE_PATHS", "CODE_AGENT_ALLOW_SENSITIVE_PATHS", agent.get("allow_sensitive_paths", False))
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


def _legacy_config_path(env: Mapping[str, str]) -> Path:
    base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "code-agent" / "config.toml"


def _environment_value(
    env: Mapping[str, str], primary: str, legacy: str, default: Any = None
) -> Any:
    if primary in env:
        return env[primary]
    if legacy in env:
        return env[legacy]
    return default


def _protocol(value: object) -> ApiProtocol:
    try:
        return ApiProtocol(_text(value, "api"))
    except ValueError:
        raise LocalConfigError("api must be responses, chat_completions, or anthropic_messages") from None


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LocalConfigError(f"{field} must be non-empty text")
    return value
