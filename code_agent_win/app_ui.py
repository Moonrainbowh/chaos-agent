from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace

from code_agent.interfaces.capability_view import ModePermissionView
from code_agent.interfaces._diff_parser import DiffScope
from code_agent.interfaces.diff_view import DiffSourceDocument
from code_agent.interfaces.rewind_models import RewindPreviewSource
from code_agent.interfaces.terminal_display import DisplayKind
from code_agent.interfaces.windows_tui import WindowsTerminalApp
from code_agent.interfaces.mode_control import ModeSummary
from code_agent.orchestration.models import ModeSnapshot
from code_agent.orchestration.plugin_extensions import PluginModeCatalog
from code_agent_win.foreground_tasks import IntegratedForegroundTaskController


__all__ = (
    "GitDiffAdapter",
    "IntegratedForegroundTaskController",
    "ModeAwareWindowsTerminalApp",
    "PluginModeControl",
    "TaskScopedGitDiffAdapter",
)


class ModeAwareWindowsTerminalApp(WindowsTerminalApp):
    def __init__(
        self,
        *args: object,
        capability: ModePermissionView,
        rewind: RewindPreviewSource | None = None,
        plugin_errors: tuple[str, ...] = (),
        recover_pending: object | None = None,
        startup: object | None = None,
        runtime_selection: object | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._capability = capability
        self._plugin_errors = plugin_errors
        self.rewind = rewind
        self._announced = False
        self._recover_pending = recover_pending
        self._startup = startup
        self.runtime_selection = runtime_selection
        self._recovered = False

    def update_capability(self, capability: ModePermissionView) -> None:
        if capability.runtime_summary is None and self._capability.runtime_summary:
            capability = replace(
                capability,
                runtime_summary=self._capability.runtime_summary,
            )
        self._capability = capability

    @property
    def host_runtime_summary(self) -> str | None:
        return self._capability.runtime_summary

    async def run(self, *, thread_id: str | None = None) -> None:
        if not self._recovered and self._startup is not None:
            await self._startup()
            self._recovered = True
        elif not self._recovered and self._recover_pending is not None:
            await self._recover_pending()
            self._recovered = True
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

    async def read_diff(
        self, paths: tuple[str, ...] = ()
    ) -> tuple[DiffSourceDocument, ...]:
        if self._git is None:
            return ()
        snapshot = await asyncio.to_thread(self._git.diff_snapshot, paths)
        return _diff_documents(snapshot)


class TaskScopedGitDiffAdapter:
    def __init__(
        self,
        runtime: object,
        active_task_id: object,
        fallback: GitDiffAdapter,
    ) -> None:
        self._runtime = runtime
        self._active_task_id = active_task_id
        self._fallback = fallback

    async def read_diff(
        self, paths: tuple[str, ...] = ()
    ) -> tuple[DiffSourceDocument, ...]:
        task_id = self._active_task_id()
        if task_id is None:
            return await self._fallback.read_diff(paths)
        await self._runtime.hydrate_bindings()
        try:
            services = self._runtime.services_for_root(
                self._runtime.root_for_task(task_id)
            )
        except KeyError:
            return await self._fallback.read_diff(paths)
        if services.git is None:
            return ()
        snapshot = await asyncio.to_thread(services.git.diff_snapshot, paths)
        return _diff_documents(snapshot)


def _diff_documents(snapshot: object) -> tuple[DiffSourceDocument, ...]:
    return tuple(
        DiffSourceDocument(scope, getattr(snapshot, name), True)
        for scope, name in (
            (DiffScope.STAGED, "staged"),
            (DiffScope.UNSTAGED, "unstaged"),
            (DiffScope.UNTRACKED, "untracked"),
        )
        if getattr(snapshot, name)
    )


class PluginModeControl:
    def __init__(
        self,
        base: object,
        catalog: PluginModeCatalog,
        identifiers: tuple[str, ...],
        apply: object,
        plugin_digests: set[str],
    ) -> None:
        self._base, self._catalog = base, catalog
        self._identifiers, self._apply = identifiers, apply
        self._current: ModeSummary | None = None
        self._plugin_digests = plugin_digests
        self._current_digest: str | None = None
        self._current_base_mode: str | None = None

    @property
    def current(self) -> ModeSummary:
        return self._current or self._base.current

    def list(self) -> tuple[ModeSummary, ...]:
        plugin = tuple(
            self._summary(self._catalog.resolve(identifier))
            for identifier in self._identifiers
        )
        return self._base.list() + plugin

    async def refresh(self, identifiers: tuple[str, ...]) -> None:
        refreshed = tuple(dict.fromkeys(identifiers))
        if any(not isinstance(item, str) or "." not in item for item in refreshed):
            raise ValueError("plugin mode ids must be namespaced")
        if self._current is not None:
            if self._current_base_mode is None:
                raise RuntimeError("plugin mode base is unavailable")
            await self._base.use(self._current_base_mode, idle=True)
            self._clear_plugin_projection()
        self._identifiers = refreshed

    async def use(self, name: str, *, idle: bool) -> ModeSummary:
        if "." not in name:
            result = await self._base.use(name, idle=idle)
            self._clear_plugin_projection()
            return result
        if not idle:
            raise RuntimeError("mode switching is available only when idle")
        contributed = self._catalog.resolve(name)
        definition = replace(
            contributed.base.definition,
            prompt_policy=contributed.prompt_policy,
            tool_names=contributed.tool_names,
            reasoning_effort=contributed.reasoning_effort,
            description=(
                contributed.base.definition.description
                + f" Plugin mode {contributed.identifier}."
            ),
        )
        digest = hashlib.sha256(
            (
                contributed.base.digest
                + contributed.identifier
                + contributed.digest
                + str(contributed.generation)
            ).encode("utf-8")
        ).hexdigest()
        snapshot = ModeSnapshot(
            definition,
            contributed.base.model,
            contributed.base.oracle_model,
            digest,
        )
        self._plugin_digests.add(digest)
        try:
            await self._apply(snapshot)
        except Exception:
            self._plugin_digests.discard(digest)
            raise
        if self._current_digest is not None and self._current_digest != digest:
            self._plugin_digests.discard(self._current_digest)
        self._current_digest = digest
        self._current_base_mode = contributed.base.definition.mode.value
        self._current = self._summary(contributed)
        return self._current

    def _clear_plugin_projection(self) -> None:
        if self._current_digest is not None:
            self._plugin_digests.discard(self._current_digest)
        self._current_digest = None
        self._current_base_mode = None
        self._current = None
    @staticmethod
    def _summary(mode: object) -> ModeSummary:
        return ModeSummary(
            mode.identifier, mode.base.model, mode.reasoning_effort.value
        )
