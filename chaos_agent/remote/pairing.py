from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path

from ..app_paths import product_state_root
from code_agent.authentication.store_lock import StoreLock


class PairingStore:
    """Single-device pairing with a durable, digest-only device credential."""

    def __init__(self, storage_path: Path | None = None) -> None:
        self._storage_path = storage_path or product_state_root() / "remote" / "devices.json"
        self._token_digest: bytes | None = None

    def issue_token(self) -> str:
        raw = secrets.token_bytes(5)
        encoded = raw.hex().upper()
        token = "-".join(encoded[index : index + 4] for index in range(0, len(encoded), 4))
        self._token_digest = self._digest(token)
        return token

    def pair(self, token: str) -> str:
        with StoreLock(self._storage_path):
            if not isinstance(token, str) or self._token_digest is None or not hmac.compare_digest(self._token_digest, self._digest(token)):
                raise PermissionError("invalid or expired pairing token")
            credential = secrets.token_urlsafe(32)
            self._save_device_digest(self._digest(credential))
            self._token_digest = None
            return credential

    def authenticate(self, credential: str | None) -> bool:
        # Read the atomically replaced file so a separate revoke CLI takes effect.
        digest = self._load_device_digest()
        return bool(isinstance(credential, str) and credential and digest is not None and hmac.compare_digest(digest, self._digest(credential)))

    def revoke(self) -> None:
        with StoreLock(self._storage_path):
            self._save_device_digest(None)

    def _load_device_digest(self) -> bytes | None:
        try:
            document = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError):
            return None
        digest = document.get("device_digest") if isinstance(document, dict) and document.get("version") == 1 else None
        if not isinstance(digest, str):
            return None
        try:
            value = bytes.fromhex(digest)
        except ValueError:
            return None
        return value if len(value) == hashlib.sha256().digest_size else None

    def _save_device_digest(self, digest: bytes | None) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        document = {
            "version": 1,
            "device_digest": digest.hex() if digest else None,
        }
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self._storage_path.parent,
            prefix=f"{self._storage_path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                json.dump(document, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self._storage_path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _digest(value: str) -> bytes:
        return hashlib.sha256(value.encode("utf-8")).digest()
