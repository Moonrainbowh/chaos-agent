from __future__ import annotations

import hashlib
import hmac
import secrets


class PairingStore:
    """Single-device, in-memory pairing for a manually started local Host."""

    def __init__(self) -> None:
        self._token_digest: bytes | None = None
        self._device_digest: bytes | None = None

    def issue_token(self) -> str:
        raw = secrets.token_bytes(5)
        encoded = raw.hex().upper()
        token = "-".join(encoded[index : index + 4] for index in range(0, len(encoded), 4))
        self._token_digest = self._digest(token)
        self._device_digest = None
        return token

    def pair(self, token: str) -> str:
        if self._token_digest is None or not hmac.compare_digest(self._token_digest, self._digest(token)):
            raise PermissionError("invalid or expired pairing token")
        self._token_digest = None
        credential = secrets.token_urlsafe(32)
        self._device_digest = self._digest(credential)
        return credential

    def authenticate(self, credential: str | None) -> bool:
        return bool(credential and self._device_digest is not None and hmac.compare_digest(self._device_digest, self._digest(credential)))

    def revoke(self) -> None:
        self._device_digest = None

    @staticmethod
    def _digest(value: str) -> bytes:
        return hashlib.sha256(value.encode("utf-8")).digest()
