from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from code_agent.context.repo_index import RepoIndexService
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.interfaces.terminal_display import DisplayKind
from code_agent.mcp.registry import McpController
from code_agent.orchestration.models import ModeSnapshot
from code_agent.plugins.registry import PluginHost
from code_agent.workflows.service import WorkflowService
from code_agent_win.action_dispatcher import RootActionDispatcher
from code_agent_win.app_ui import ModeAwareWindowsTerminalApp
from code_agent_win.rewind_runtime import RewindRuntime
from code_agent_win.subagents import SubagentRuntime


@dataclass
class Application:
    controller: AgentController
    foreground_tasks: ForegroundTaskController
    tui: ModeAwareWindowsTerminalApp
    dispatcher: RootActionDispatcher
    model: object
    mcp: McpController | None = None
    mode: ModeSnapshot | None = None
    plugins: PluginHost | None = None
    subagents: SubagentRuntime | None = None
    rewind: RewindRuntime | None = None
    repo_index: RepoIndexService | None = None
    workflows: WorkflowService | None = None
    workspace_runtime: object | None = None
    attachment_store: object | None = None
    attachment_ingestor: object | None = None
    runtime_selection: object | None = None
    peers: object | None = None
    sessions: object | None = None
    workspace_root: Path | None = None
    authentication: object | None = None
    model_preferences: object | None = None
    restore_model_selection: bool = False
    _closed: bool = field(default=False, init=False, repr=False)
    _model_selection_restored: bool = field(default=False, init=False, repr=False)

    async def startup(self) -> None:
        if self.workspace_runtime is not None:
            await self.workspace_runtime.startup()
        if self.restore_model_selection and not self._model_selection_restored:
            self._model_selection_restored = True
            message, warning = await self._restore_model_selection()
            if message:
                self.tui._append(DisplayKind.WARNING if warning else DisplayKind.METADATA, message)

    async def _restore_model_selection(self) -> tuple[str | None, bool]:
        preferences = self.model_preferences
        runtime = self.runtime_selection
        if preferences is None or runtime is None:
            return None, False
        preference = preferences.load()
        if preference is None:
            return None, False
        if preference.source == "configured":
            names = {name for name, _, _ in runtime.profiles()}
            if preference.profile not in names:
                preferences.clear()
                return "Last selected model is no longer configured; using the configured default.", True
            try:
                await runtime.use(profile=preference.profile, idle=True)
            except Exception:
                return "Could not restore the last model; using the configured default.", True
            return f"Restored model: {preference.profile}", False
        if self.authentication is None:
            return None, False
        instruction = (
            f"{preference.provider}:{preference.authentication} "
            f"{shlex.quote(preference.model)}"
        )
        try:
            profile = await self.authentication.select_model(instruction)
            await runtime.use(profile=profile, idle=True)
        except Exception as error:
            if getattr(
                self.authentication, "is_unavailable_saved_model", lambda _: False
            )(error):
                preferences.clear()
                return "Last selected saved-login model is unavailable; using the configured default.", True
            return "Could not restore the last saved-login model; using the configured default.", True
        return f"Restored model: {preference.model}", False

    def workspace_root_for(self, task_id: str) -> Path:
        if self.workspace_runtime is not None:
            return self.workspace_runtime.root_for_task(task_id)
        raise RuntimeError("workspace runtime is unavailable")

    def runtime_root_for(self, task_id: str) -> Path:
        return self.workspace_root_for(task_id)

    def verification_root_for(self, task_id: str) -> Path:
        return self.workspace_root_for(task_id)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: BaseException | None = None
        for resource in (self.peers, self.subagents, self.model, self.mcp):
            close = getattr(resource, "aclose", None)
            if close is None:
                continue
            try:
                await close()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        close_workspace = getattr(self.workspace_runtime, "close", None)
        if close_workspace is not None:
            try:
                close_workspace()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error
