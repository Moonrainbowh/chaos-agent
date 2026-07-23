from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PluginReloadState(str, Enum):
    IDLE = "idle"
    STAGED = "staged"


@dataclass(frozen=True)
class PluginStatus:
    plugin_id: str
    namespace: str
    version: str
    digest: str
    source: str
    trusted: bool
    enabled: bool

    def __post_init__(self) -> None:
        for name in ("plugin_id", "namespace", "version", "digest", "source"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-blank text")
        if not isinstance(self.trusted, bool) or not isinstance(self.enabled, bool):
            raise TypeError("trusted and enabled must be bool")


@dataclass(frozen=True)
class PluginHostStatus:
    generation: int
    reload_state: PluginReloadState
    plugins: tuple[PluginStatus, ...]
    staged_plugins: tuple[PluginStatus, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 0
        ):
            raise ValueError("generation must be a non-negative integer")
        if not isinstance(self.reload_state, PluginReloadState):
            raise TypeError("reload_state must be PluginReloadState")
        for name in ("plugins", "staged_plugins"):
            values = tuple(getattr(self, name))
            if any(not isinstance(value, PluginStatus) for value in values):
                raise TypeError(f"{name} must contain PluginStatus values")
            object.__setattr__(self, name, values)


__all__ = ["PluginHostStatus", "PluginReloadState", "PluginStatus"]
