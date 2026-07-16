from __future__ import annotations

import asyncio

from code_agent.interfaces.capability_view import ModePermissionView
from code_agent.interfaces.terminal_display import DisplayKind
from code_agent.interfaces.windows_tui import WindowsTerminalApp
from code_agent.interfaces.task_controller import ForegroundTaskController


class ModeAwareWindowsTerminalApp(WindowsTerminalApp):
    def __init__(self, *args: object, capability: ModePermissionView, plugin_errors: tuple[str, ...] = (), **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._capability = capability
        self._plugin_errors = plugin_errors
        self._announced = False

    async def run(self, *, thread_id: str | None = None) -> None:
        if not self._announced:
            for line in self._capability.lines():
                self._append(DisplayKind.METADATA, line)
            if self._plugin_errors:
                self._append(
                    DisplayKind.WARNING,
                    f"plugins: {len(self._plugin_errors)} contribution(s) unavailable",
                )
            self._announced = True
        await super().run(thread_id=thread_id)


class GitDiffAdapter:
    def __init__(self, git: object | None) -> None:
        self._git = git

    async def read_diff(self, paths: tuple[str, ...] = ()) -> str:
        if self._git is None:
            return ""
        return await asyncio.to_thread(self._git.diff, paths)


class IntegratedForegroundTaskController(ForegroundTaskController):
    def __init__(self, *args: object, subagents: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._subagents = subagents

    async def events(self, task_id: str, prompt: str | None = None):
        token = self._subagents.activate(task_id)
        try:
            async for event in super().events(task_id, prompt):
                yield event
        finally:
            await self._subagents.release(task_id)
            self._subagents.reset(token)
