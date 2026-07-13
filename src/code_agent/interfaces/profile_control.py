from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from code_agent.providers.config import ModelProfile


@dataclass(frozen=True)
class ProfileSummary:
    name: str
    model: str
    protocol: str


class ProfileControl:
    """Select only known profiles at an idle boundary through an injected rebuild."""

    def __init__(self, profiles: Mapping[str, ModelProfile], current: str, apply: Callable[[ModelProfile], None]) -> None:
        self._profiles, self._current, self._apply = dict(profiles), current, apply
        if current not in self._profiles: raise ValueError("current profile is not configured")

    @property
    def current(self) -> ProfileSummary: return _summary(self._profiles[self._current])

    def list(self) -> tuple[ProfileSummary, ...]: return tuple(_summary(profile) for profile in self._profiles.values())

    def use(self, name: str, *, idle: bool) -> ProfileSummary:
        if not idle: raise RuntimeError("model switching is available only when idle")
        try: profile = self._profiles[name]
        except KeyError: raise ValueError("unknown configured model profile") from None
        self._apply(profile); self._current = name
        return _summary(profile)


def _summary(profile: ModelProfile) -> ProfileSummary:
    return ProfileSummary(profile.name, profile.provider.model, profile.provider.api.value)
