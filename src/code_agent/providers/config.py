from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable
from urllib.parse import unquote, urlsplit

from .errors import ProviderConfigError
from code_agent.authentication.source import StoredCredentialSource


class ApiProtocol(str, Enum):
    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat_completions"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    CODEX_RESPONSES = "codex_responses"
    GOOGLE_GENERATIVE_AI = "google_generative_ai"
    PI_MESSAGES = "pi_messages"


class InputModality(str, Enum):
    TEXT = "text"
    IMAGE = "image"


def freeze_input_modalities(
    values: Iterable[InputModality],
) -> frozenset[InputModality]:
    if isinstance(values, (str, bytes)):
        raise ProviderConfigError("input_modalities must be a modality collection")
    modalities = frozenset(values)
    if not modalities or any(
        not isinstance(item, InputModality) for item in modalities
    ):
        raise ProviderConfigError("input_modalities contains an unsupported value")
    if InputModality.TEXT not in modalities:
        raise ProviderConfigError("input_modalities must include text")
    return modalities


class ConfiguredApiKey:
    """Opaque key value loaded from a local user configuration file."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ProviderConfigError("configured API key must be non-empty")
        self._value = value

    def resolve(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return self.status

    def __deepcopy__(self, memo: dict[int, object]) -> ConfiguredApiKey:
        return self

    @property
    def status(self) -> str:
        return f"configured (...{self._value[-4:]})"


def _require_safe_base_url(value: object) -> str:
    if not isinstance(value, str):
        raise ProviderConfigError("base_url must be a string")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderConfigError("base_url must be an HTTP(S) URL without credentials")
    if any(segment in {".", ".."} for segment in unquote(parsed.path).split("/")):
        raise ProviderConfigError("base_url contains an unsafe path")
    return value.rstrip("/")


def _require_safe_path(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ProviderConfigError(f"{label} must be a string")
    parsed = urlsplit(value)
    decoded = unquote(parsed.path)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or "\\" in decoded
        or any(segment in {".", ".."} for segment in decoded.split("/"))
    ):
        raise ProviderConfigError(f"{label} must be a safe relative URL path")
    return value


def _require_positive_int(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProviderConfigError(f"{label} must be a positive integer")


def optional_token_rate(value: object, label: str) -> float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ProviderConfigError(f"{label} must be a finite non-negative number")
    return float(value)


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    model: str
    api: ApiProtocol
    api_key_env: str | None = None
    api_key_source: ConfiguredApiKey | None = field(
        default=None, repr=False, compare=False
    )
    timeout_s: float = 60.0
    max_retries: int = 2
    max_event_bytes: int = 1_048_576
    max_response_bytes: int = 8 * 1024 * 1024
    max_tool_argument_bytes: int = 1024 * 1024
    max_tool_calls: int = 64
    responses_path: str = "/v1/responses"
    chat_completions_path: str = "/v1/chat/completions"
    anthropic_messages_path: str = "/v1/messages"
    provider_id: str | None = None
    auth_source: StoredCredentialSource | None = field(default=None, repr=False, compare=False)
    pi_messages_path: str = "/messages"

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _require_safe_base_url(self.base_url))
        if not isinstance(self.model, str) or not self.model.strip():
            raise ProviderConfigError("model must be a non-empty string")
        if not isinstance(self.api, ApiProtocol):
            raise ProviderConfigError("api must be an ApiProtocol")
        has_env = isinstance(self.api_key_env, str) and bool(self.api_key_env.strip())
        if self.api_key_env is not None and not has_env:
            raise ProviderConfigError("api_key_env must be a non-empty string")
        if self.api_key_source is not None and not isinstance(
            self.api_key_source, ConfiguredApiKey
        ):
            raise ProviderConfigError("api_key_source must be a ConfiguredApiKey")
        if self.auth_source is not None and not isinstance(self.auth_source, StoredCredentialSource):
            raise ProviderConfigError("auth_source must be a StoredCredentialSource")
        if sum((has_env, self.api_key_source is not None, self.auth_source is not None)) != 1:
            raise ProviderConfigError("configure exactly one API key source")
        if self.auth_source is not None and self.provider_id != self.auth_source.provider:
            raise ProviderConfigError("credential provider must match provider_id")
        if (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, (int, float))
            or not math.isfinite(self.timeout_s)
            or self.timeout_s <= 0
        ):
            raise ProviderConfigError("timeout_s must be finite and greater than zero")
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or self.max_retries < 0
        ):
            raise ProviderConfigError("max_retries must be a non-negative integer")
        for field_name in (
            "max_event_bytes",
            "max_response_bytes",
            "max_tool_argument_bytes",
            "max_tool_calls",
        ):
            _require_positive_int(getattr(self, field_name), field_name)
        for field_name in (
            "responses_path",
            "chat_completions_path",
            "anthropic_messages_path",
            "pi_messages_path",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_safe_path(getattr(self, field_name), field_name),
            )

    def resolve_api_key(self, env: Mapping[str, str] | None = None) -> str:
        if self.auth_source is not None:
            raise ProviderConfigError("Stored credentials must be resolved asynchronously")
        if self.api_key_source is not None:
            return self.api_key_source.resolve()
        source = os.environ if env is None else env
        value = source.get(self.api_key_env or "")
        if not isinstance(value, str) or not value.strip():
            raise ProviderConfigError(
                f"Required API key environment variable is missing: {self.api_key_env}"
            )
        return value

    @property
    def key_status(self) -> str:
        if self.auth_source is not None:
            return self.auth_source.status
        if self.api_key_source is not None:
            return self.api_key_source.status
        return f"environment ({self.api_key_env})"


@dataclass(frozen=True)
class ModelProfile:
    """One selectable model configuration without storing secret values."""

    name: str
    provider: ProviderConfig
    context_window: int
    max_output_tokens: int
    max_agent_rounds: int = 50
    max_tool_calls: int = 128
    max_tool_calls_per_round: int = 50
    input_modalities: frozenset[InputModality] = field(
        default_factory=lambda: frozenset({InputModality.TEXT})
    )
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    context_policy: object | None = None
    api_input_tokens: int | None = None

    def __post_init__(self) -> None:
        from code_agent.context_windows.policy import ApiContextLimits, WindowPolicy
        if self.context_policy is not None:
            if not isinstance(self.context_policy, WindowPolicy):
                raise ProviderConfigError("context_policy must be a WindowPolicy")
            ApiContextLimits(self.context_window, self.max_output_tokens,
                             self.api_input_tokens).input_cap(self.context_policy)
        if not isinstance(self.name, str) or not self.name.strip():
            raise ProviderConfigError("profile name must be a non-empty string")
        if not isinstance(self.provider, ProviderConfig):
            raise ProviderConfigError("profile provider must be a ProviderConfig")
        for name in (
            "context_window",
            "max_output_tokens",
            "max_agent_rounds",
            "max_tool_calls",
            "max_tool_calls_per_round",
        ):
            try:
                _require_positive_int(getattr(self, name), name)
            except ProviderConfigError:
                raise
        object.__setattr__(
            self,
            "input_modalities",
            freeze_input_modalities(self.input_modalities),
        )
        rates = (self.input_cost_per_million, self.output_cost_per_million)
        if (rates[0] is None) != (rates[1] is None):
            raise ProviderConfigError("configure both input and output token rates")
        for label, value in zip(
            ("input_cost_per_million", "output_cost_per_million"), rates
        ):
            optional_token_rate(value, label)


class ModelProfileResolver:
    def __init__(self, profiles: Mapping[str, ModelProfile], default_name: str) -> None:
        if not isinstance(default_name, str) or not default_name.strip():
            raise ProviderConfigError("default model name must be non-empty")
        copied = dict(profiles)
        if not copied or any(name != profile.name for name, profile in copied.items()):
            raise ProviderConfigError("profiles must be keyed by their names")
        if default_name not in copied:
            raise ProviderConfigError("default model is not configured")
        self._profiles = copied
        self._default_name = default_name

    def select(self, name: str | None) -> ModelProfile:
        selected = self._default_name if name is None else name
        if not isinstance(selected, str) or not selected.strip():
            raise ProviderConfigError("model name must be non-empty")
        try:
            return self._profiles[selected]
        except KeyError:
            raise ProviderConfigError(f"unknown model profile: {selected}") from None
