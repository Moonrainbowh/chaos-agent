from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from code_agent.workspace.windows_paths import require_supported_windows_path


_VERSION = 1
_MAX_BYTES = 4_096
_FILE_NAME = "last-model-selection.json"


@dataclass(frozen=True)
class ModelSelectionPreference:
    source: str
    profile: str | None = None
    provider: str | None = None
    authentication: str | None = None
    model: str | None = None

    @classmethod
    def configured(cls, profile: str) -> ModelSelectionPreference:
        return cls("configured", profile=profile)

    @classmethod
    def saved_login(
        cls, provider: str, authentication: str, model: str
    ) -> ModelSelectionPreference:
        return cls(
            "saved_login",
            provider=provider,
            authentication=authentication,
            model=model,
        )


class ModelSelectionPreferenceStore:
    """Store one non-secret Windows-user model selection with atomic replacement."""

    def __init__(self, root: Path) -> None:
        self._path = root / _FILE_NAME
        require_supported_windows_path(self._path, operation="model selection preference")

    def load(self) -> ModelSelectionPreference | None:
        try:
            if self._path.stat().st_size > _MAX_BYTES:
                return None
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return _parse(value)

    def save(self, preference: ModelSelectionPreference) -> None:
        value = _encode(preference)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
            os.replace(temporary, self._path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def clear(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass


def _encode(preference: ModelSelectionPreference) -> dict[str, object]:
    parsed = _parse({
        "version": _VERSION,
        "source": preference.source,
        "profile": preference.profile,
        "provider": preference.provider,
        "authentication": preference.authentication,
        "model": preference.model,
    })
    if parsed != preference:
        raise ValueError("invalid model selection preference")
    return {
        "version": _VERSION,
        "source": preference.source,
        "profile": preference.profile,
        "provider": preference.provider,
        "authentication": preference.authentication,
        "model": preference.model,
    }


def _parse(value: object) -> ModelSelectionPreference | None:
    if not isinstance(value, dict) or value.get("version") != _VERSION:
        return None
    source = value.get("source")
    if source == "configured":
        profile = _text(value.get("profile"))
        return ModelSelectionPreference.configured(profile) if profile else None
    if source == "saved_login":
        provider = _text(value.get("provider"))
        authentication = _text(value.get("authentication"))
        model = _text(value.get("model"))
        if provider and authentication in {"oauth", "api_key"} and model:
            return ModelSelectionPreference.saved_login(
                provider, authentication, model
            )
    return None


def _text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        return None
    return value
