from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from urllib.parse import unquote, urlsplit

from .errors import ProviderConfigError


class ApiProtocol(str, Enum):
    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat_completions"
    ANTHROPIC_MESSAGES = "anthropic_messages"


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


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    model: str
    api: ApiProtocol
    api_key_env: str
    timeout_s: float = 60.0
    max_retries: int = 2
    max_event_bytes: int = 1_048_576
    max_response_bytes: int = 8 * 1024 * 1024
    max_tool_argument_bytes: int = 1024 * 1024
    max_tool_calls: int = 64
    responses_path: str = "/v1/responses"
    chat_completions_path: str = "/v1/chat/completions"
    anthropic_messages_path: str = "/v1/messages"

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _require_safe_base_url(self.base_url))
        if not isinstance(self.model, str) or not self.model.strip():
            raise ProviderConfigError("model must be a non-empty string")
        if not isinstance(self.api, ApiProtocol):
            raise ProviderConfigError("api must be an ApiProtocol")
        if not isinstance(self.api_key_env, str) or not self.api_key_env.strip():
            raise ProviderConfigError("api_key_env must be a non-empty string")
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
        ):
            object.__setattr__(
                self,
                field_name,
                _require_safe_path(getattr(self, field_name), field_name),
            )

    def resolve_api_key(self, env: Mapping[str, str] | None = None) -> str:
        source = os.environ if env is None else env
        value = source.get(self.api_key_env)
        if not isinstance(value, str) or not value.strip():
            raise ProviderConfigError(
                f"Required API key environment variable is missing: {self.api_key_env}"
            )
        return value
