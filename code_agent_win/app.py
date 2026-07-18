from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from code_agent.config.loader import load_runtime_config
from code_agent.context.builder import WorkspaceContextBuilder
from code_agent.context.compaction import DeterministicCompactor
from code_agent.context.models import ContextConfig
from code_agent.context.repo_index import RepoIndexService
from code_agent.context.repo_map import RepoMapBuilder, RepoMapViewCache
from code_agent.context.repo_scan import RepoFileScanner
from code_agent.context.rules import RuleLoader
from code_agent.core.engine import AgentEngine
from code_agent.core.limits import EngineLimits
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.interfaces.capability_view import ModePermissionView, PermissionSummary
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.mode_control import ModeControl
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
    freeze_mode,
    main_tools_for_mode,
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
from code_agent_win.workspace_context import workspace_uses_repo_map


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
    repo_index: RepoIndexService | None = None

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
    repo_scanner = RepoFileScanner(files)
    repo_index = RepoIndexService(files, scan_file=repo_scanner.scan)
    repo_view_cache = RepoMapViewCache()
    repo_map_enabled = workspace_uses_repo_map(root, git_available=git is not None)
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
    mode_snapshots = {
        mode: modes.freeze(mode, configured_profiles)
        for mode in AgentMode
    }

    skills = SkillActivation(SkillRegistry.discover(root))

    def context_for(mode: ModeSnapshot) -> object:
        prompt = windows_system_prompt(git is not None) + "\n\n" + mode_prompt(mode)
        config = ContextConfig(
            root,
            root,
            prompt,
            repo_map_enabled=repo_map_enabled,
        )
        context = WorkspaceContextBuilder(
            config,
            RuleLoader(guard, files, config),
            RepoMapBuilder(
                files,
                config,
                index=repo_index,
                view_cache=repo_view_cache,
            ),
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

    def invalidate_workspace_context(paths: Sequence[str]) -> None:
        repo_index.invalidate(paths)
        files.invalidate_inventory()

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
        invalidate_cache=invalidate_workspace_context,
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
    )
    dispatcher.subagents = subagents
    plugin_names = tuple(tool.name for tool in plugin_bridge.definitions())
    def main_dispatcher_for(selected: ModeSnapshot) -> RestrictedDispatcher:
        main_tools = main_tools_for_mode(selected.definition.tool_names, plugin_names)
        return RestrictedDispatcher(dispatcher, main_tools)

    main_dispatcher = main_dispatcher_for(snapshot)
    model = _model_client(initial.provider)
    initial_runner = _engine_for(
        model, initial, context_for(snapshot), main_dispatcher, sessions, root, snapshot
    )
    controller = AgentController(initial_runner)

    active_snapshot = snapshot
    build_snapshot = snapshot

    async def build_runtime(profile: ModelProfile) -> ProviderRuntime:
        client = _model_client(profile.provider)
        runner = _engine_for(
            client,
            profile,
            context_for(build_snapshot),
            main_dispatcher_for(build_snapshot),
            sessions,
            root,
            build_snapshot,
        )
        return ProviderRuntime(profile, client, runner)

    manager = ProviderRuntimeManager(
        configured_profiles,
        ProviderRuntime(initial, model, initial_runner),
        build_runtime,
        controller.replace_runner,
    )

    application_ref: list[Application] = []
    tui_ref: list[ModeAwareWindowsTerminalApp] = []

    async def apply_mode(selected: ModeSnapshot) -> None:
        nonlocal active_snapshot, build_snapshot
        previous = active_snapshot
        build_snapshot = selected
        try:
            await manager.switch(selected.definition.profile_id, idle=True)
        except Exception:
            build_snapshot = previous
            raise
        active_snapshot = selected
        if application_ref:
            application_ref[0].mode = selected
        if tui_ref:
            tui_ref[0].update_capability(
                ModePermissionView(
                    selected,
                    PermissionSummary(runtime_config.approval_mode, str(root), False, True),
                    applies_next_task=True,
                )
            )

    mode_control = ModeControl(
        mode_snapshots,
        snapshot.definition.mode,
        apply_mode,
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
        if name != active_snapshot.definition.profile_id:
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
        modes=mode_control,
        skills=skills,
        mcp=mcp,
        diff_source=GitDiffAdapter(git),
        capability=capability,
        plugin_errors=plugin_errors,
    )
    tui_ref.append(tui)
    subagents.subscribe(lambda view: tui.interactions.observe_agent(tui, view))
    application = Application(
        controller,
        foreground,
        tui,
        dispatcher,
        manager,
        mcp,
        snapshot,
        plugin_host,
        subagents,
        repo_index,
    )
    application_ref.append(application)
    return application


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
