from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from code_agent.config.loader import load_runtime_config
from code_agent.context.builder import WorkspaceContextBuilder
from code_agent.context.cache import RepoMapCache
from code_agent.context.compaction import DeterministicCompactor
from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.core.engine import AgentEngine
from code_agent.core.limits import EngineLimits
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.interfaces.capability_view import ModePermissionView, PermissionSummary
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.profile_control import ProfileControl
from code_agent.interfaces.task_controller import ForegroundTaskController
from code_agent.mcp.official_sdk import OfficialMcpSdkAdapter
from code_agent.mcp.registry import McpController, McpRegistry
from code_agent.mcp.stdio_manager import StdioMcpManager
from code_agent.orchestration.models import AgentDefinition, AgentMode, ModeSnapshot
from code_agent.plugins.registry import PluginHost
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.providers.config import ModelProfile
from code_agent.providers.runtime_manager import ProviderRuntime, ProviderRuntimeManager
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.sessions.legacy_migration import migrate_legacy_session_database
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.skills.registry import SkillActivation, SkillContextBuilder, SkillRegistry
from code_agent.verification.local_adapter import LocalVerificationAdapter
from code_agent.verification.task_service import LedgerTaskVerificationService
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard

from code_agent_win.action_dispatcher import RootActionDispatcher
from code_agent_win.agent_modes import (
    build_mode_registry,
    default_child_mode,
    freeze_mode,
    mode_prompt,
)
from code_agent_win.app_ui import (
    GitDiffAdapter,
    IntegratedForegroundTaskController,
    ModeAwareWindowsTerminalApp,
)
from code_agent_win.plugin_runtime import PluginToolBridge, load_plugins
from code_agent_win.runtime_support import host_risks, model_client, replace_model
from code_agent_win.subagents import EngineChildRunner, RestrictedDispatcher, SubagentRuntime
from code_agent_win.tool_support import discover_git_workspace, windows_system_prompt
from code_agent_win.tools import tool_definitions


_model_client = model_client


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


def create_application(
    workspace_root: Path | None = None,
    *,
    model_name: str | None = None,
    profile_name: str | None = None,
    mode_name: str | None = None,
) -> Application:
    if profile_name is not None and (not isinstance(profile_name, str) or not profile_name.strip()):
        raise ValueError("profile_name must be non-blank text")
    root = (workspace_root or Path.cwd()).resolve()
    guard = WorkspacePathGuard(root)
    files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
    git = discover_git_workspace(root)
    cache = RepoMapCache(root)
    runtime_config = load_runtime_config(cli_profile=profile_name)
    configured_profiles = {profile.name: profile for profile in runtime_config.profiles}
    modes, _ = build_mode_registry(configured_profiles, runtime_config.profile)
    snapshot = freeze_mode(modes, configured_profiles, mode_name)
    initial_name = snapshot.definition.profile_id
    initial = configured_profiles[initial_name]
    if model_name is not None:
        initial = replace_model(initial, model_name)
        configured_profiles[initial_name] = initial
        snapshot = freeze_mode(modes, configured_profiles, snapshot.definition.mode.value)

    skills = SkillActivation(SkillRegistry.discover(root))

    def context_for(mode: ModeSnapshot) -> object:
        prompt = windows_system_prompt(git is not None) + "\n\n" + mode_prompt(mode)
        config = ContextConfig(root, root, prompt)
        context = WorkspaceContextBuilder(
            config,
            RuleLoader(guard, files, config),
            RepoMapBuilder(files, config, cache=cache),
            DeterministicCompactor(config),
        )
        return SkillContextBuilder(context, skills)

    approvals = ApprovalBroker()
    mcp_risks: dict[str, str] = {"delegate_agent": "write"}
    mcp = McpController(
        McpRegistry(runtime_config.mcp_servers),
        StdioMcpManager(lambda server: OfficialMcpSdkAdapter(tool_risks=server.tool_risks)),
        mcp_risks,
    )
    plugin_host, plugin_errors = load_plugins(
        root,
        modes,
        host_actions=tuple(
            tool.name for tool in tool_definitions(include_git=git is not None)
        ),
        host_risks=host_risks(),
        controllers=("review", "task", "session"),
    )
    plugin_bridge = PluginToolBridge(plugin_host)
    mcp_risks.update(plugin_bridge.risk_map())
    policy = ActionPolicy(
        PolicyConfig(runtime_config.approval_mode, workspace_root=root, mcp_risks=mcp_risks)
    )
    dispatcher = RootActionDispatcher(
        files,
        WorkspaceEditor(guard),
        policy,
        approvals,
        git=git,
        runtime=WindowsLocalRuntime(root),
        verification=LocalVerificationAdapter(root),
        mcp=mcp,
        plugins=plugin_bridge,
        invalidate_cache=cache.invalidate,
    )
    sessions = SQLiteSessionRepository(_session_path())

    def child_engine(agent: AgentDefinition) -> tuple[AgentEngine, object]:
        profile = configured_profiles[agent.mode.definition.profile_id]
        client = _model_client(profile.provider)
        restricted = RestrictedDispatcher(dispatcher, agent.effective_tools)
        engine = _engine_for(
            client, profile, context_for(agent.mode), restricted, sessions, root, agent.mode
        )
        return engine, client

    subagents = SubagentRuntime(
        EngineChildRunner(child_engine),
        modes,
        configured_profiles,
        default_child_mode(snapshot.definition.mode),
    )
    dispatcher.subagents = subagents
    plugin_names = tuple(tool.name for tool in plugin_bridge.definitions())
    main_tools = snapshot.definition.tool_names + (
        plugin_names if snapshot.definition.mode in {AgentMode.HIGH, AgentMode.ULTRA} else ()
    )
    main_dispatcher = RestrictedDispatcher(dispatcher, main_tools)
    model = _model_client(initial.provider)
    initial_runner = _engine_for(
        model, initial, context_for(snapshot), main_dispatcher, sessions, root, snapshot
    )
    controller = AgentController(initial_runner)

    async def build_runtime(profile: ModelProfile) -> ProviderRuntime:
        client = _model_client(profile.provider)
        runner = _engine_for(
            client, profile, context_for(snapshot), main_dispatcher, sessions, root, snapshot
        )
        return ProviderRuntime(profile, client, runner)

    manager = ProviderRuntimeManager(
        configured_profiles,
        ProviderRuntime(initial, model, initial_runner),
        build_runtime,
        controller.replace_runner,
    )

    async def apply_profile(profile: ModelProfile) -> None:
        if profile.name != snapshot.definition.profile_id:
            raise RuntimeError("profile is bound by the selected mode; choose another mode for the next task")
        await manager.switch(profile.name, idle=True)

    profile_control = ProfileControl(
        configured_profiles, snapshot.definition.profile_id, apply_profile
    )

    def profile_facts() -> tuple[str, str, str, str]:
        profile = manager.current.profile
        return (
            profile.name,
            profile.provider.model,
            profile.provider.api.value,
            urlsplit(profile.provider.base_url).hostname or "unknown",
        )

    async def resolve_profile(name: str) -> None:
        if name != snapshot.definition.profile_id:
            raise RuntimeError("recorded task mode profile is unavailable")
        if manager.current.profile.name != name:
            await manager.switch(name, idle=True)

    foreground = IntegratedForegroundTaskController(
        controller,
        sessions,
        root,
        profile_supplier=profile_facts,
        profile_resolver=resolve_profile,
        subagents=subagents,
    )
    capability = ModePermissionView(
        snapshot,
        PermissionSummary(runtime_config.approval_mode, str(root), False, True),
    )
    tui = ModeAwareWindowsTerminalApp(
        controller,
        approvals,
        sessions=sessions,
        evidence=sessions,
        history=sessions,
        tasks=foreground,
        profiles=profile_control,
        skills=skills,
        mcp=mcp,
        diff_source=GitDiffAdapter(git),
        capability=capability,
        plugin_errors=plugin_errors,
    )
    subagents.subscribe(lambda view: tui.interactions.observe_agent(tui, view))
    return Application(
        controller, foreground, tui, dispatcher, manager, mcp, snapshot, plugin_host, subagents
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


def _session_path() -> Path:
    base = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    directory = Path(base) / "chaos-agent"
    legacy = Path(base) / "code-agent" / "sessions.sqlite3"
    directory.mkdir(parents=True, exist_ok=True)
    current = directory / "sessions.sqlite3"
    if not current.exists() and legacy.exists():
        migrate_legacy_session_database(legacy, current)
    return current
