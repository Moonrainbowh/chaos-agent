from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def environment_value(
    env: Mapping[str, str], primary: str, legacy: str, default: Any = None
) -> Any:
    if primary in env:
        return env[primary]
    if legacy in env:
        return env[legacy]
    return default
