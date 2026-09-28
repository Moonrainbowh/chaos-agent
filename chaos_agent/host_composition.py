from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from pathlib import Path
from weakref import WeakKeyDictionary

from code_agent.interfaces.interaction import InteractionBroker
from code_agent.mcp.official_sdk import OfficialMcpSdkAdapter
from code_agent.mcp.registry import McpController, McpRegistry
from code_agent.mcp.stdio_manager import StdioMcpManager
from code_agent.orchestration.models import AgentDefinition, AgentMode, ModeSnapshot
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.plugins.registry import ContributionSnapshot, PluginHost
from code_agent.thread_intelligence.authorization import ThreadAuthorization
from code_agent.thread_intelligence.tools import ThreadIntelligenceTools
from code_agent_win.child_runner import EngineChildRunner
from code_agent_win.plugin_runtime import (
    PluginToolBridge,
    load_plugins,
    plugin_event_risks,
)
from code_agent_win.runtime_support import host_risks
from code_agent_win.subagents import RestrictedDispatcher, SubagentRuntime
from code_agent_win.tools import tool_definitions
from code_agent_win.task_dispatcher import TaskScopedDispatcher
from code_agent_win.workspace_mutation_pool import WorkspaceMutationPool
from code_agent_win.workspace_models import WorkspaceServices
from code_agent_win.workspace_runtime import ManagedWorkspaceRuntime


PluginDiscovery = Callable[
    [], tuple[ContributionSnapshot, tuple[str, ...]]
]
HostComposition = tuple[
    object,
    McpController,
    PluginHost,
    tuple[str, ...],
    PluginToolBridge,
    InteractionBroker,
    PluginDiscovery,
    "PluginRuntimeBindings",
]


class PluginRuntimeBindings:
    """Refresh policy and mode-scoped dispatchers from the active snapshot."""

    def __init__(
        self, dispatcher: object, host: PluginHost, bridge: PluginToolBridge
    ) -> None:
        self._dispatcher = dispatcher
        self._host = host
        self._bridge = bridge
        self._risk_names = set(self._plugin_risks())
        self._restricted: WeakKeyDictionary[
            RestrictedDispatcher, Callable[[], Sequence[str]]
        ] = WeakKeyDictionary()

    def bind(
        self,
        dispatcher: RestrictedDispatcher,
        allowed: Callable[[], Sequence[str]],
    ) -> RestrictedDispatcher:
        if not isinstance(dispatcher, RestrictedDispatcher):
            raise TypeError("dispatcher must be RestrictedDispatcher")
        if not callable(allowed):
            raise TypeError("allowed must be callable")
        dispatcher.replace_allowed(allowed())
        self._restricted[dispatcher] = allowed
        return dispatcher

    def refresh(self) -> None:
        current = self._plugin_risks()
        risks = self._dispatcher.policy.config.mcp_risks
        if not isinstance(risks, MutableMapping):
            raise TypeError("central policy risks must be mutable")
        for name in self._risk_names:
            risks.pop(name, None)
        risks.update(current)
        self._risk_names = set(current)
        for dispatcher, allowed in tuple(self._restricted.items()):
            dispatcher.replace_allowed(allowed())

    def _plugin_risks(self) -> dict[str, str]:
        return self._bridge.risk_map() | plugin_event_risks(self._host)


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
    peers: object | None = None,
    capture: object | None = None,
    mutations: WorkspaceMutationPool | None = None,
    process_rules: object | None = None,
) -> HostComposition:
    risks: dict[str, str] = {
        "delegate_agent": "write",
        "search_threads": "read",
        "read_thread": "read",
        "list_agents": "read",
        "send_message": "write",
    }
    mcp = _compose_mcp(runtime_config, risks)
    discover_plugins = _plugin_discovery(root, modes, services, mcp)
    plugin_snapshot, plugin_errors = discover_plugins()
    plugin_host = PluginHost(plugin_snapshot)
    plugin_bridge = PluginToolBridge(plugin_host)
    risks.update(plugin_bridge.risk_map())
    risks.update(plugin_event_risks(plugin_host))
    dispatcher = _compose_dispatcher(
        root=root,
        services=services,
        workspace_runtime=workspace_runtime,
        runtime_config=runtime_config,
        risks=risks,
        approvals=approvals,
        mcp=mcp,
        plugin_bridge=plugin_bridge,
        capture=capture,
        mutations=mutations,
        sessions=sessions,
        thread_binding=thread_binding,
        peers=peers,
        process_rules=process_rules,
    )
    bindings = PluginRuntimeBindings(dispatcher, plugin_host, plugin_bridge)
    return (
        dispatcher,
        mcp,
        plugin_host,
        plugin_errors,
        plugin_bridge,
        InteractionBroker(),
        discover_plugins,
        bindings,
    )


def _compose_mcp(runtime_config: object, risks: dict[str, str]) -> McpController:
    return McpController(
        McpRegistry(runtime_config.mcp_servers),
        StdioMcpManager(
            lambda server: OfficialMcpSdkAdapter(tool_risks=server.tool_risks)
        ),
        risks,
    )


def _plugin_discovery(
    root: Path,
    modes: object,
    services: WorkspaceServices,
    mcp: McpController,
) -> PluginDiscovery:
    actions = tuple(
        tool.name
        for tool in tool_definitions(include_git=services.git is not None)
    ) + ("search_threads", "read_thread")
    risks = host_risks()
    mcp_tools = _configured_mcp_targets(mcp)

    def discover() -> tuple[ContributionSnapshot, tuple[str, ...]]:
        discovered, errors = load_plugins(
            root,
            modes,
            host_actions=actions,
            host_risks=risks,
            mcp_tools=mcp_tools,
            controllers=("review", "task", "session", "workflow"),
        )
        return discovered.snapshot, errors

    return discover


def _compose_dispatcher(
    *,
    root: Path,
    services: WorkspaceServices,
    workspace_runtime: ManagedWorkspaceRuntime,
    runtime_config: object,
    risks: dict[str, str],
    approvals: object,
    mcp: McpController,
    plugin_bridge: PluginToolBridge,
    capture: object | None,
    mutations: WorkspaceMutationPool | None,
    sessions: object,
    thread_binding: object,
    peers: object | None,
    process_rules: object | None,
) -> TaskScopedDispatcher:
    policy = ActionPolicy(
        PolicyConfig(
            runtime_config.approval_mode,
            workspace_root=root,
            mcp_risks=risks,
        )
    )
    threads = ThreadIntelligenceTools(sessions, ThreadAuthorization(sessions))
    return TaskScopedDispatcher(
        workspace_runtime,
        services,
        policy,
        approvals,
        mcp=mcp,
        plugins=plugin_bridge,
        capture=capture,
        mutations=mutations or WorkspaceMutationPool(services, capture),
        threads=threads,
        caller_thread=thread_binding.current,
        peers=peers,
        process_rules=process_rules,
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
) -> tuple[SubagentRuntime, tuple[str, ...], set[str]]:
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
    return subagents, _configured_mcp_targets(dispatcher.mcp), set()


def _configured_mcp_targets(mcp: object) -> tuple[str, ...]:
    return tuple(
        f"mcp.{server.name}.{tool_name}"
        for server in mcp.status()
        for tool_name in server.tool_risks
    )
