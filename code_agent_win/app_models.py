from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.mcp.registry import McpController
from code_agent.orchestration.models import ModeSnapshot
from code_agent.plugins.registry import PluginHost

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

    async def aclose(self) -> None:
        if self.subagents is not None:
            await self.subagents.aclose()
        close = getattr(self.model, "aclose", None)
        if close is not None:
            await close()
        if self.mcp is not None:
            await self.mcp.aclose()


@dataclass
class FactoryHost:
    root: Path
    files: Any
    guard: Any
    git: Any
    cache: Any
    runtime_config: Any
    profiles: Any
    modes: Any
    mode: Any
    initial: Any
    skills: Any
    approvals: Any
    mcp: Any
    plugin_host: Any
    plugin_errors: Any
    plugin_bridge: Any
    dispatcher: Any
    sessions: Any


@dataclass
class FactoryExecution:
    subagents: Any
    main_dispatcher: Any
    manager: Any
    controller: Any


__all__ = ["Application"]
