from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from .config import ModelProfile


@dataclass(frozen=True)
class ProviderRuntime:
    profile: ModelProfile
    client: object
    runner: object


BuildRuntime = Callable[[ModelProfile], Awaitable[ProviderRuntime]]
ReplaceRunner = Callable[[object], None]


class ProviderRuntimeManager:
    """Own the provider-bound runtime and replace it atomically at idle boundaries."""

    def __init__(self, profiles: Mapping[str, ModelProfile], current: ProviderRuntime, build: BuildRuntime, replace: ReplaceRunner) -> None:
        self._profiles, self._current, self._build, self._replace = dict(profiles), current, build, replace
        if current.profile.name not in self._profiles:
            raise ValueError("current profile is not configured")

    @property
    def current(self) -> ProviderRuntime:
        return self._current

    async def switch(self, name: str, *, idle: bool) -> ProviderRuntime:
        if not idle:
            raise RuntimeError("model switching is available only when idle")
        try:
            profile = self._profiles[name]
        except KeyError:
            raise ValueError("unknown configured model profile") from None
        replacement = await self._build(profile)
        try:
            self._replace(replacement.runner)
        except Exception:
            await _close(replacement.client)
            raise
        previous, self._current = self._current, replacement
        await _close(previous.client)
        return replacement

    async def aclose(self) -> None:
        await _close(self._current.client)


async def _close(value: object) -> None:
    close = getattr(value, "aclose", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result
