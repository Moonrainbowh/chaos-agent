from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from .models import AuthError
from .store_lock import StoreLock


def append_profile(path: Path, name: str, fields: dict[str, object]) -> None:
    """Append one explicit profile, retaining all existing TOML and comments."""
    if not name.strip() or any(ord(char) < 32 for char in name):
        raise AuthError("Invalid profile name")
    for key in ("context_window", "max_output_tokens"):
        if type(fields.get(key)) is not int or fields[key] <= 0:
            raise AuthError(f"{key} must be a positive integer")
    with StoreLock(path):
        try:
            original = path.read_bytes().decode("utf-8") if path.exists() else ""
            document = tomllib.loads(original)
            providers = document.get("providers", {})
            if not isinstance(providers, dict) or name in providers:
                raise AuthError("Profile already exists; choose a new --profile name")
            text = original + ("\n\n" if original and not original.endswith("\n\n") else "")
            if not document:
                text += "[default]\nprovider = " + json.dumps(name, ensure_ascii=False) + "\n\n"
            text += "[providers." + json.dumps(name, ensure_ascii=False) + "]\n"
            text += "\n".join(f"{key} = {_toml(value)}" for key, value in fields.items()) + "\n"
            parsed = tomllib.loads(text)
            if parsed["providers"][name] != fields:
                raise AuthError("Profile serialization did not round trip")
            _atomic_write(path, text)
        except (OSError, tomllib.TOMLDecodeError):
            raise AuthError("Cannot update configuration; existing file was preserved") from None


def _toml(value: object) -> str:
    if type(value) is int or isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return json.dumps(value, ensure_ascii=False)
    raise AuthError("Unsupported profile field type")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".profile-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
