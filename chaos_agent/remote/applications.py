from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, Callable

from .catalog import canonical_root


def _create_application(root: Path, mode_name: str | None = None) -> object:
    from chaos_agent.app import create_application

    return create_application(workspace_root=root, mode_name=mode_name)


class RemoteApplications:
    """Lazily own one alternate project application, sharing product storage."""

    def __init__(self, application: object, factory: Callable[[Path], Any] | None = None) -> None:
        self.primary = application
        value = getattr(application, "workspace_root", None)
        self.primary_root = canonical_root(value) if value is not None else None
        self.factory = factory or self._create_for_root
        self.child: object | None = None
        self.child_root: Path | None = None
        self._enable_approvals(application)

    @staticmethod
    def _enable_approvals(application: object) -> None:
        dispatcher = getattr(application, "dispatcher", None)
        if dispatcher is not None:
            dispatcher.interactive = True

    def _create_for_root(self, root: Path) -> object:
        runtime = getattr(self.primary, "runtime_selection", None)
        mode_name = runtime.current.legacy_mode if runtime is not None else None
        return _create_application(root, mode_name)

    async def for_root(self, root: Path) -> object:
        root = canonical_root(root)
        if root == self.primary_root:
            return self.primary
        if root == self.child_root and self.child is not None:
            await self._inherit_selection(self.child)
            return self.child
        await self.aclose()
        application = await asyncio.to_thread(self.factory, root)
        if inspect.isawaitable(application):
            application = await application
        try:
            startup = getattr(application, "startup", None)
            if callable(startup):
                await startup()
            await self._inherit_selection(application)
            self._enable_approvals(application)
        except BaseException:
            close = getattr(application, "aclose", None)
            if callable(close):
                await close()
            raise
        self.child, self.child_root = application, root
        return application

    async def _inherit_selection(self, application: object) -> None:
        source = getattr(self.primary, "runtime_selection", None)
        target = getattr(application, "runtime_selection", None)
        if source is None or target is None:
            return
        selection = source.current
        available = {item[0] for item in target.profiles()}
        if selection.profile not in available:
            auth = getattr(application, "authentication", None)
            if auth is None:
                raise RuntimeError("selected runtime is unavailable")
            await auth.restore_profile(selection.profile)
        if target.current.legacy_mode != selection.legacy_mode:
            modes = getattr(getattr(application, "tui", None), "modes", None)
            use_mode = getattr(modes, "use", None)
            if not callable(use_mode):
                raise RuntimeError("selected agent mode is unavailable")
            await use_mode(selection.legacy_mode, idle=True)
        await target.use(
            profile=selection.profile, topology=selection.topology,
            reasoning_effort=selection.reasoning_effort, idle=True,
        )

    async def aclose(self) -> None:
        application, self.child = self.child, None
        self.child_root = None
        close = getattr(application, "aclose", None)
        if callable(close):
            await close()
