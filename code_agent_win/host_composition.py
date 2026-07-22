from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from code_agent.interfaces.interaction import InteractionBroker
from code_agent.mcp.official_sdk import OfficialMcpSdkAdapter
from code_agent.mcp.registry import McpController, McpRegistry
from code_agent.mcp.stdio_manager import StdioMcpManager
from code_agent.orchestration.models import AgentDefinition, AgentMode, ModeSnapshot
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.thread_intelligence.authorization import ThreadAuthorization
from code_agent.thread_intelligence.tools import ThreadIntelligenceTools
from code_agent_win.child_runner import EngineChildRunner
from code_agent_win.plugin_runtime import (
    PluginToolBridge,
    load_plugins,
    plugin_event_risks,
)
from code_agent_win.runtime_support import host_risks
from code_agent_win.subagents import SubagentRuntime
from code_agent_win.tools import tool_definitions
from code_agent_win.workspace_runtime import (
    ManagedWorkspaceRuntime,
    TaskScopedDispatcher,
    WorkspaceServices,
)


def compose_host(
    *,
    root: Path,
    services: WorkspaceServices,
    workspace_runtime: ManagedWorkspaceRuntime,
    runtime_config: object,
    modes: object,
    sessions: object,
    approvals: object,
    thread_binding: object,
) -> tuple[object, McpController, object, tuple[str, ...], PluginToolBridge, InteractionBroker]:
    risks: dict[str, str] = {
        "delegate_agent": "write",
        "search_threads": "read",
        "read_thread": "read",
    }
    mcp = McpController(
        McpRegistry(runtime_config.mcp_servers),
        StdioMcpManager(
            lambda server: OfficialMcpSdkAdapter(
                tool_risks=server.tool_risks
            )
        ),
        risks,
    )
    plugin_host, plugin_errors = load_plugins(
        root,
        modes,
        host_actions=tuple(
            tool.name
            for tool in tool_definitions(include_git=services.git is not None)
        )
        + ("search_threads", "read_thread"),
        host_risks=host_risks(),
        controllers=("review", "task", "session", "workflow"),
    )
    plugin_bridge = PluginToolBridge(plugin_host)
    risks.update(plugin_bridge.risk_map())
    risks.update(plugin_event_risks(plugin_host))
    dispatcher = TaskScopedDispatcher(
        workspace_runtime,
        services,
        ActionPolicy(
            PolicyConfig(
                runtime_config.approval_mode,
                workspace_root=root,
                mcp_risks=risks,
            )
        ),
        approvals,
        mcp=mcp,
        plugins=plugin_bridge,
        threads=ThreadIntelligenceTools(
            sessions, ThreadAuthorization(sessions)
        ),
        caller_thread=thread_binding.current,
    )
    return (
        dispatcher,
        mcp,
        plugin_host,
        plugin_errors,
        plugin_bridge,
        InteractionBroker(),
    )


def compose_subagents(
    *,
    child_engine: Callable[[AgentDefinition], tuple[object, object]],
    dispatcher: object,
    sessions: object,
    thread_binding: object,
    modes: object,
    profiles: Mapping[str, object],
    plugin_host: object,
    mode_snapshots: Mapping[AgentMode, ModeSnapshot],
) -> tuple[SubagentRuntime, tuple[str, ...], tuple[str, ...], set[str]]:
    subagents = SubagentRuntime(
        EngineChildRunner(
            child_engine,
            sessions=sessions,
            parent_thread=thread_binding.current,
        ),
        modes,
        profiles,
        plugin_host=plugin_host,
        mode_snapshots=mode_snapshots,
    )
    dispatcher.subagents = subagents
    plugin_names = tuple(
        tool.name for tool in dispatcher.plugins.definitions()
    )
    mcp_names = tuple(
        f"mcp.{server.name}.{tool_name}"
        for server in dispatcher.mcp.status()
        for tool_name in server.tool_risks
    )
    return subagents, plugin_names, mcp_names, set()
