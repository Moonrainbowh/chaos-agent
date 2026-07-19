from __future__ import annotations
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from code_agent.context.cache import RepoMapCache
from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.core.engine import AgentEngine
from code_agent.core.limits import EngineLimits
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.interfaces.capability_view import ModePermissionView, PermissionSummary
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.profile_control import ProfileControl
from code_agent.mcp.official_sdk import OfficialMcpSdkAdapter
from code_agent.mcp.registry import McpController, McpRegistry
from code_agent.mcp.stdio_manager import StdioMcpManager
from code_agent.orchestration.models import AgentDefinition, AgentMode, ModeSnapshot
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.providers.config import ModelProfile
from code_agent.providers.runtime_manager import ProviderRuntime, ProviderRuntimeManager
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.skills.registry import SkillActivation, SkillRegistry
from code_agent.verification.local_adapter import LocalVerificationAdapter
from code_agent.verification.task_service import LedgerTaskVerificationService
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent_win.action_dispatcher import RootActionDispatcher
from code_agent_win.agent_modes import build_mode_registry, default_child_mode, freeze_mode, mode_prompt
from code_agent_win.app_models import Application, FactoryExecution, FactoryHost
from code_agent_win.app_ui import GitDiffAdapter, IntegratedForegroundTaskController, ModeAwareWindowsTerminalApp
from code_agent_win.plugin_runtime import PluginToolBridge, load_plugins
from code_agent_win.runtime_support import host_risks, replace_model
from code_agent_win.subagents import EngineChildRunner, RestrictedDispatcher, SubagentRuntime
from code_agent_win.tool_support import discover_git_workspace, windows_system_prompt
from code_agent_win.tools import tool_definitions
def create_application(
    workspace_root: Path | None,
    *,
    model_name: str | None,
    profile_name: str | None,
    mode_name: str | None,
    load_config: Callable[..., Any],
    model_factory: Callable[[object], object],
    session_path_factory: Callable[[], Path],
    context_factory: Callable[..., object],
) -> Application:
    if profile_name is not None and (
        not isinstance(profile_name, str) or not profile_name.strip()
    ):
        raise ValueError("profile_name must be non-blank text")
    root = (workspace_root or Path.cwd()).resolve()
    host = _build_host(
        root, model_name, profile_name, mode_name, load_config, session_path_factory
    )
    execution = _build_execution(host, model_factory, context_factory)
    foreground = _foreground(host, execution)
    profiles = _profile_control(host, execution)
    tui = _tui(host, execution, foreground, profiles)
    execution.subagents.subscribe(lambda view: tui.interactions.observe_agent(tui, view))
    return Application(
        execution.controller, foreground, tui, host.dispatcher, execution.manager,
        host.mcp, host.mode, host.plugin_host, execution.subagents,
    )
def _build_host(
    root: Path,
    model_name: str | None,
    profile_name: str | None,
    mode_name: str | None,
    load_config: Callable[..., Any],
    session_path_factory: Callable[[], Path],
) -> FactoryHost:
    guard = WorkspacePathGuard(root)
    files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
    git = discover_git_workspace(root)
    cache = RepoMapCache(root)
    config = load_config(cli_profile=profile_name)
    profiles = {profile.name: profile for profile in config.profiles}
    modes, _ = build_mode_registry(profiles, config.profile)
    mode = freeze_mode(modes, profiles, mode_name)
    initial = profiles[mode.definition.profile_id]
    if model_name is not None:
        initial = replace_model(initial, model_name)
        profiles[initial.name] = initial
        mode = freeze_mode(modes, profiles, mode.definition.mode.value)
    return _host_integrations(
        root, guard, files, git, cache, config, profiles, modes, mode, initial,
        session_path_factory,
    )
def _host_integrations(
    root: Path,
    guard: WorkspacePathGuard,
    files: WorkspaceFiles,
    git: object,
    cache: RepoMapCache,
    config: Any,
    profiles: dict[str, ModelProfile],
    modes: Any,
    mode: ModeSnapshot,
    initial: ModelProfile,
    session_path_factory: Callable[[], Path],
) -> FactoryHost:
    skills = SkillActivation(SkillRegistry.discover(root))
    approvals = ApprovalBroker()
    risks: dict[str, str] = {"delegate_agent": "write"}
    manager = StdioMcpManager(lambda server: OfficialMcpSdkAdapter(tool_risks=server.tool_risks))
    mcp = McpController(McpRegistry(config.mcp_servers), manager, risks)
    plugin_host, errors = load_plugins(
        root,
        modes,
        host_actions=tuple(
            tool.name for tool in tool_definitions(include_git=git is not None)
        ),
        host_risks=host_risks(),
        controllers=("review", "task", "session"),
    )
    bridge = PluginToolBridge(plugin_host)
    risks.update(bridge.risk_map())
    policy = ActionPolicy(
        PolicyConfig(config.approval_mode, workspace_root=root, mcp_risks=risks)
    )
    dispatcher = RootActionDispatcher(
        files, WorkspaceEditor(guard), policy, approvals,
        git=git,
        runtime=WindowsLocalRuntime(root),
        verification=LocalVerificationAdapter(root),
        mcp=mcp,
        plugins=bridge,
        invalidate_cache=cache.invalidate,
    )
    sessions = SQLiteSessionRepository(session_path_factory())
    return FactoryHost(
        root, files, guard, git, cache, config, profiles, modes, mode, initial, skills,
        approvals, mcp, plugin_host, errors, bridge, dispatcher, sessions,
    )


def _context_for(
    host: FactoryHost,
    mode: ModeSnapshot,
    context_factory: Callable[..., object],
) -> object:
    prompt = windows_system_prompt(host.git is not None) + "\n\n" + mode_prompt(mode)
    config = ContextConfig(host.root, host.root, prompt)
    rules = RuleLoader(host.guard, host.files, config)
    repo_map = RepoMapBuilder(host.files, config, cache=host.cache)
    return context_factory(config, rules, repo_map, host.skills, host.sessions)


def _build_execution(
    host: FactoryHost,
    model_factory: Callable[[object], object],
    context_factory: Callable[..., object],
) -> FactoryExecution:
    def child_engine(agent: AgentDefinition) -> tuple[AgentEngine, object]:
        profile = host.profiles[agent.mode.definition.profile_id]
        client = model_factory(profile.provider)
        restricted = RestrictedDispatcher(host.dispatcher, agent.effective_tools)
        engine = _engine_for(
            client, profile, _context_for(host, agent.mode, context_factory), restricted,
            host.sessions, host.root, agent.mode,
        )
        return engine, client

    subagents = SubagentRuntime(
        EngineChildRunner(child_engine), host.modes, host.profiles,
        default_child_mode(host.mode.definition.mode),
    )
    host.dispatcher.subagents = subagents
    plugin_names = tuple(tool.name for tool in host.plugin_bridge.definitions())
    extras = plugin_names if host.mode.definition.mode in {AgentMode.HIGH, AgentMode.ULTRA} else ()
    main_tools = host.mode.definition.tool_names + extras
    main_dispatcher = RestrictedDispatcher(host.dispatcher, main_tools)
    model = model_factory(host.initial.provider)
    runner = _engine_for(
        model, host.initial, _context_for(host, host.mode, context_factory), main_dispatcher,
        host.sessions, host.root, host.mode,
    )
    controller = AgentController(runner)

    async def build_runtime(profile: ModelProfile) -> ProviderRuntime:
        client = model_factory(profile.provider)
        next_runner = _engine_for(
            client, profile, _context_for(host, host.mode, context_factory), main_dispatcher,
            host.sessions, host.root, host.mode,
        )
        return ProviderRuntime(profile, client, next_runner)

    manager = ProviderRuntimeManager(
        host.profiles, ProviderRuntime(host.initial, model, runner),
        build_runtime, controller.replace_runner,
    )
    return FactoryExecution(subagents, main_dispatcher, manager, controller)


def _profile_control(
    host: FactoryHost, execution: FactoryExecution
) -> ProfileControl:
    async def apply_profile(profile: ModelProfile) -> None:
        if profile.name != host.mode.definition.profile_id:
            raise RuntimeError(
                "profile is bound by the selected mode; "
                "choose another mode for the next task"
            )
        await execution.manager.switch(profile.name, idle=True)

    return ProfileControl(host.profiles, host.mode.definition.profile_id, apply_profile)


def _foreground(
    host: FactoryHost,
    execution: FactoryExecution,
) -> IntegratedForegroundTaskController:
    def profile_facts() -> tuple[str, str, str, str]:
        profile = execution.manager.current.profile
        return (
            profile.name,
            profile.provider.model,
            profile.provider.api.value,
            urlsplit(profile.provider.base_url).hostname or "unknown",
        )

    async def resolve_profile(name: str) -> None:
        if name != host.mode.definition.profile_id:
            raise RuntimeError("recorded task mode profile is unavailable")
        if execution.manager.current.profile.name != name:
            await execution.manager.switch(name, idle=True)

    return IntegratedForegroundTaskController(
        execution.controller,
        host.sessions,
        host.root,
        profile_supplier=profile_facts,
        profile_resolver=resolve_profile,
        subagents=execution.subagents,
    )


def _tui(
    host: FactoryHost,
    execution: FactoryExecution,
    foreground: IntegratedForegroundTaskController,
    profiles: ProfileControl,
) -> ModeAwareWindowsTerminalApp:
    capability = ModePermissionView(
        host.mode,
        PermissionSummary(host.runtime_config.approval_mode, str(host.root), False, True),
    )
    return ModeAwareWindowsTerminalApp(
        execution.controller,
        host.approvals,
        sessions=host.sessions,
        evidence=host.sessions,
        history=host.sessions,
        tasks=foreground,
        profiles=profiles,
        skills=host.skills,
        mcp=host.mcp,
        diff_source=GitDiffAdapter(host.git),
        capability=capability,
        plugin_errors=host.plugin_errors,
    )


def _engine_for(
    model: object,
    profile: ModelProfile,
    context: object,
    dispatcher: object,
    sessions: object,
    workspace_root: Path,
    mode: ModeSnapshot,
) -> AgentEngine:
    mode_limits = mode.definition.limits
    limits = EngineLimits(
        min(profile.max_agent_rounds, mode_limits.max_agent_rounds),
        min(profile.max_tool_calls, mode_limits.max_tool_calls),
        min(profile.max_tool_calls_per_round, mode_limits.max_tool_calls_per_round),
        min(profile.context_window + profile.max_output_tokens, mode_limits.max_total_tokens),
        mode_limits.max_assistant_chars,
    )
    return AgentEngine(
        model,
        context,
        dispatcher,
        sessions,
        limits=limits,
        model_name=profile.provider.model,
        verification=LedgerTaskVerificationService(workspace_root, sessions),
    )


__all__ = ["create_application"]
