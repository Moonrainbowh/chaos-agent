from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from code_agent.context.repo_index import RepoIndexService
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.task_controller import ForegroundTaskController
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
    _closed: bool = field(default=False, init=False, repr=False)

    async def startup(self) -> None:
        if self.workspace_runtime is not None:
            await self.workspace_runtime.startup()

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
        for resource in (self.subagents, self.model, self.mcp):
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
