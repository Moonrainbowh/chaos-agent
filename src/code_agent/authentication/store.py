from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Mapping

from .models import AuthError, Credential
from .protection import protect, unprotect
from .store_lock import StoreLock


def default_auth_path(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    override = values.get("CHAOS_AUTH_FILE")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise AuthError("CHAOS_AUTH_FILE must be absolute")
        return path
    base = values.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "chaos-agent" / "credentials.dat"


def validate_provider_id(provider: str) -> str:
    if not isinstance(provider, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", provider):
        raise AuthError("Invalid provider identifier")
    return provider


class CredentialStore:
    """Atomic user-private store. Unlocked methods require the store lock."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = default_auth_path() if path is None else Path(path)

    def lock(self) -> StoreLock:
        return StoreLock(self.path)

    def _read(self) -> dict[str, Credential]:
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_size > 4 * 1024 * 1024:
                raise AuthError("Credential file exceeds size limit")
            raw = json.loads(unprotect(self.path.read_bytes()))
            if raw.get("version") != 1 or not isinstance(raw.get("credentials"), dict):
                raise AuthError("Invalid credential file format")
            values = {}
            for key, value in raw["credentials"].items():
                provider, kind = key.rsplit(":", 1)
                credential = Credential(**value)
                if kind != credential.kind:
                    raise AuthError("Credential key/type mismatch")
                values[f"{validate_provider_id(provider)}:{kind}"] = credential
            return values
        except (OSError, ValueError, TypeError, AttributeError):
            raise AuthError("Cannot read credential store; existing file was preserved") from None

    def _write(self, credentials: Mapping[str, Credential]) -> None:
        raw = {"version": 1, "credentials": {k: asdict(v) for k, v in credentials.items()}}
        try:
            encoded = protect(json.dumps(raw, ensure_ascii=False).encode("utf-8"))
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(prefix=".credentials-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except OSError:
            raise AuthError("Cannot save credential store") from None

    def get(self, provider: str, kind: str | None = None) -> Credential | None:
        validate_provider_id(provider)
        with self.lock():
            values = self._read()
            if kind is not None:
                return values.get(f"{provider}:{kind}")
            return values.get(f"{provider}:oauth") or values.get(f"{provider}:api_key")

    def set(self, provider: str, credential: Credential) -> None:
        validate_provider_id(provider)
        if not isinstance(credential, Credential):
            raise AuthError("Invalid credential")
        with self.lock():
            values = self._read()
            values[f"{provider}:{credential.kind}"] = credential
            self._write(values)

    def remove(self, provider: str, kind: str | None = None) -> bool:
        validate_provider_id(provider)
        with self.lock():
            values = self._read()
            keys = [f"{provider}:{item}" for item in (kind,) if item] if kind else [f"{provider}:oauth", f"{provider}:api_key"]
            found = False
            for key in keys:
                found = (values.pop(key, None) is not None) or found
            if found:
                self._write(values)
            return found

    def status(self) -> tuple[tuple[str, str, float | None], ...]:
        with self.lock():
            return tuple((k.rsplit(":", 1)[0], v.kind, v.expires_at) for k, v in sorted(self._read().items()))
