from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping


class AuthError(ValueError):
    """An authentication failure whose message contains no credential values."""


@dataclass(frozen=True)
class Credential:
    """Private credential; expires_at is Unix seconds, None means no expiry."""

    kind: str
    access: str = field(repr=False)
    refresh: str = field(default="", repr=False)
    expires_at: float | None = None
    extra: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.kind not in {"api_key", "oauth"}:
            raise AuthError("Unknown credential kind")
        if not isinstance(self.access, str) or not self.access.strip():
            raise AuthError("Credential has no access token")
        if not isinstance(self.refresh, str) or not isinstance(self.extra, Mapping):
            raise AuthError("Invalid credential metadata")
        if self.expires_at is not None and (
            isinstance(self.expires_at, bool)
            or not isinstance(self.expires_at, (int, float))
            or not math.isfinite(self.expires_at)
        ):
            raise AuthError("Invalid credential expiration")
