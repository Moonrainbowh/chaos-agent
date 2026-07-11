from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Optional


_WINDOWS_DEFAULT_NAMES = (
    "PATH",
    "SystemRoot",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
)
_REDACTED = "[REDACTED]"
_SENSITIVE_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "COOKIE", "API_KEY")
_SENSITIVE_COMPACT_MARKERS = (
    "APIKEY",
    "ACCESSTOKEN",
    "CLIENTSECRET",
    "PRIVATEKEY",
)


def _validate_environment_name(name: object) -> str:
    if not isinstance(name, str):
        raise TypeError("environment variable names must be strings")
    if not name or "=" in name or "\x00" in name:
        raise ValueError("environment variable names must be non-blank names")
    return name


def _casefold_environment(environment: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(environment, Mapping):
        raise TypeError("environment must be a mapping")
    folded: dict[str, str] = {}
    for raw_name, value in environment.items():
        name = _validate_environment_name(raw_name)
        if not isinstance(value, str):
            raise TypeError("environment variable values must be strings")
        folded[name.casefold()] = value
    return folded


def sanitize_environment(
    host_env: Mapping[str, str],
    allowed_names: Iterable[str] = (),
    explicit_env: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Return the minimal approved Windows child-process environment."""

    if isinstance(allowed_names, (str, bytes)):
        raise TypeError("allowed_names must be an iterable of names")

    canonical = {name.casefold(): name for name in _WINDOWS_DEFAULT_NAMES}
    for raw_name in allowed_names:
        name = _validate_environment_name(raw_name)
        canonical.setdefault(name.casefold(), name)

    inherited = _casefold_environment(host_env)
    explicit = (
        _casefold_environment(explicit_env) if explicit_env is not None else {}
    )
    sanitized: dict[str, str] = {}
    for folded_name, output_name in canonical.items():
        if folded_name in explicit:
            sanitized[output_name] = explicit[folded_name]
        elif folded_name in inherited:
            sanitized[output_name] = inherited[folded_name]
    return sanitized


def _is_sensitive_key(key: str) -> bool:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", separated).upper().strip("_")
    compact = normalized.replace("_", "")
    return normalized in {"AUTHORIZATION", "PROXY_AUTHORIZATION"} or any(
        marker in normalized for marker in _SENSITIVE_MARKERS
    ) or any(
        marker in compact for marker in _SENSITIVE_COMPACT_MARKERS
    )


def redact_sensitive(value: Any) -> Any:
    """Copy a nested value while replacing values under sensitive keys."""

    if isinstance(value, Mapping):
        return {
            key: (
                _REDACTED
                if isinstance(key, str) and _is_sensitive_key(key)
                else redact_sensitive(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value
