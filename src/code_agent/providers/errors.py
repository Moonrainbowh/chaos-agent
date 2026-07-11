from __future__ import annotations

import re
from collections.abc import Iterable, Mapping


_AUTHORIZATION = re.compile(
    r"(?i)\b((?:proxy-)?authorization\s*[:=]\s*)[^;\r\n]*"
)
_BEARER = re.compile(r"(?i)bearer\s+[^\s;,]+")
_API_KEY = re.compile(
    r"(?i)(?:api[-_ ]?key|x-api-key)\s*[:=]\s*[^\s;,]+"
)
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "proxyauthorization",
        "xapikey",
        "apikey",
        "token",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "secret",
        "clientsecret",
        "password",
        "passwd",
        "cookie",
        "setcookie",
    }
)
_SENSITIVE_SUFFIXES = ("authorization", "apikey", "token", "secret", "password", "cookie")


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    return normalized in _SENSITIVE_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES)


def _copy_redacted(value: object, active: set[int]) -> object:
    if not isinstance(value, (Mapping, list, tuple)):
        return value
    identity = id(value)
    if identity in active:
        return "[CIRCULAR]"
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            return {
                key: "[REDACTED]" if _is_sensitive_key(key) else _copy_redacted(item, active)
                for key, item in value.items()
            }
        copied = [_copy_redacted(item, active) for item in value]
        return tuple(copied) if isinstance(value, tuple) else copied
    finally:
        active.remove(identity)


def _safe_text(message: object) -> str:
    if not isinstance(message, (Mapping, list, tuple)):
        return str(message)
    try:
        return str(_copy_redacted(message, set()))
    except Exception:
        return "[UNAVAILABLE STRUCTURED MESSAGE]"


def _redact(message: object, sensitive_values: Iterable[str]) -> str:
    text = _safe_text(message)
    for value in sensitive_values:
        if value:
            text = text.replace(value, "[REDACTED]")
    text = _AUTHORIZATION.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = _BEARER.sub("[REDACTED]", text)
    return _API_KEY.sub("api_key=[REDACTED]", text)


class ProviderError(RuntimeError):
    """Base provider failure whose rendered message is safe to surface."""

    def __init__(
        self,
        message: object = "Provider operation failed",
        *,
        sensitive_values: Iterable[str] = (),
    ) -> None:
        super().__init__(_redact(message, sensitive_values))


class ProviderConfigError(ProviderError):
    pass


class ProviderHTTPError(ProviderError):
    def __init__(
        self,
        status: int,
        retryable: bool,
        message: object | None = None,
        *,
        sensitive_values: Iterable[str] = (),
    ) -> None:
        self.status = status
        self.retryable = retryable
        super().__init__(
            message if message is not None else f"Provider HTTP status {status}",
            sensitive_values=sensitive_values,
        )


class ProviderProtocolError(ProviderError):
    pass


class ProviderResponseLimitError(ProviderError):
    pass
