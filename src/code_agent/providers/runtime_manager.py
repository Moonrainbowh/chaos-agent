from __future__ import annotations

import asyncio
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
        self._lock = asyncio.Lock()
        self._retirements: dict[asyncio.Task[None], object] = {}
        self._close_retry: list[object] = []
        if current.profile.name not in self._profiles:
            raise ValueError("current profile is not configured")

    @property
    def current(self) -> ProviderRuntime:
        return self._current

    def register_profile(self, profile: ModelProfile) -> None:
        """Add a validated profile without changing the active runtime."""
        if not isinstance(profile, ModelProfile):
            raise TypeError("profile must be a ModelProfile")
        existing = self._profiles.get(profile.name)
        if existing is not None and existing != profile:
            raise ValueError("profile name already belongs to another configuration")
        self._profiles[profile.name] = profile

    async def switch(self, name: str, *, idle: bool) -> ProviderRuntime:
        if not idle:
            raise RuntimeError("model switching is available only when idle")
        async with self._lock:
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
            self._retire(previous.client)
            return replacement

    async def aclose(self) -> None:
        async with self._lock:
            tracked = tuple(self._retirements.items())
            if tracked:
                await asyncio.gather(
                    *(task for task, _ in tracked), return_exceptions=True
                )
                for task, client in tracked:
                    if task.cancelled():
                        self._queue_retry(client)
                    else:
                        try:
                            task.result()
                        except BaseException:
                            self._queue_retry(client)
            retry, self._close_retry = self._close_retry, []
            clients = _unique_objects((*retry, self._current.client))
            failure: BaseException | None = None
            for client in clients:
                try:
                    await _close(client)
                except BaseException as error:
                    self._queue_retry(client)
                    if failure is None:
                        failure = error
            if failure is not None:
                raise failure

    def _retire(self, client: object) -> None:
        task = asyncio.create_task(_close(client))
        self._retirements[task] = client
        task.add_done_callback(self._retirement_finished)

    def _retirement_finished(self, task: asyncio.Task[None]) -> None:
        client = self._retirements.pop(task, None)
        if client is None:
            return
        try:
            task.result()
        except BaseException:
            self._queue_retry(client)

    def _queue_retry(self, client: object) -> None:
        if all(item is not client for item in self._close_retry):
            self._close_retry.append(client)


async def _close(value: object) -> None:
    close = getattr(value, "aclose", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


def _unique_objects(values: tuple[object, ...]) -> tuple[object, ...]:
    unique: list[object] = []
    for value in values:
        if all(item is not value for item in unique):
            unique.append(value)
    return tuple(unique)
