from __future__ import annotations

from dataclasses import dataclass
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
    repo_index: RepoIndexService | None = None
    workflows: WorkflowService | None = None
    workspace_runtime: object | None = None

    def workspace_root_for(self, task_id: str) -> Path:
        if self.workspace_runtime is not None:
            return self.workspace_runtime.root_for_task(task_id)
        raise RuntimeError("workspace runtime is unavailable")

    def runtime_root_for(self, task_id: str) -> Path:
        return self.workspace_root_for(task_id)

    def verification_root_for(self, task_id: str) -> Path:
        return self.workspace_root_for(task_id)

    async def aclose(self) -> None:
        if self.subagents is not None:
            await self.subagents.aclose()
        close = getattr(self.model, "aclose", None)
        if close is not None:
            await close()
        if self.mcp is not None:
            await self.mcp.aclose()
