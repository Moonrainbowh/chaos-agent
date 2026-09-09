from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

from .models import AuthError, Credential
from .store import CredentialStore, validate_provider_id


@dataclass(frozen=True)
class StoredCredentialSource:
    provider: str
    path: Path = field(repr=False)
    kind: str = "oauth"

    def __post_init__(self) -> None:
        validate_provider_id(self.provider)
        if self.kind not in {"oauth", "api_key"}:
            raise AuthError("Unknown authentication method")

    @property
    def status(self) -> str:
        return f"{self.kind} ({self.provider})"

    async def resolve(self) -> Credential:
        # Worker owns lock throughout refresh: cancellation cannot leak a lock
        # or race logout into resurrecting a removed credential.
        return await asyncio.to_thread(self._resolve)

    def _resolve(self) -> Credential:
        store = CredentialStore(self.path)
        with store.lock():
            values = store._read()
            key = f"{self.provider}:{self.kind}"
            value = values.get(key)
            if value is None or value.kind != self.kind:
                raise AuthError(f"Run chaos-agent auth login {self.provider} to authenticate")
            if value.kind == "oauth" and value.expires_at is not None and value.expires_at <= time.time() + 60:
                from .oauth import refresh
                value = asyncio.run(refresh(self.provider, value))
                values[key] = value
                store._write(values)
            return value
