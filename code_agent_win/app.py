from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from code_agent.config.loader import load_runtime_config
from code_agent.context.repo_index import RepoIndexService
from code_agent.context.repo_map import RepoMapViewCache
from code_agent.context.repo_scan import RepoFileScanner
from code_agent.core.engine import AgentEngine
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.interfaces.capability_view import ModePermissionView, PermissionSummary
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.mode_control import ModeControl
from code_agent.interfaces.permission_control import PermissionControl
from code_agent.orchestration.models import AgentDefinition, AgentMode, ModeSnapshot
from code_agent.orchestration.plugin_extensions import PluginModeCatalog
from code_agent.providers.config import ModelProfile
from code_agent.providers.runtime_manager import ProviderRuntime, ProviderRuntimeManager
from code_agent.policy.engine import ActionPolicy
from code_agent.policy.models import ApprovalMode
from code_agent.sessions.legacy_migration import migrate_legacy_session_database
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.skills.controller import SkillController
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard

from code_agent_win.application_context import RuntimeContextFactory, engine_for
from code_agent_win.application_model import Application
from code_agent_win.action_dispatcher import RootActionDispatcher
from code_agent_win.host_composition import compose_host, compose_subagents
from code_agent_win.agent_modes import (
    build_mode_registry,
    freeze_mode,
    main_tools_for_mode,
)
from code_agent_win.app_ui import ModeAwareWindowsTerminalApp, PluginModeControl
from code_agent_win.runtime_support import model_client, replace_model
from code_agent_win.runtime_extensions import SkillApprovalAdapter, ThreadRuntimeBinding
from code_agent_win.subagents import RestrictedDispatcher
from code_agent_win.tool_support import discover_git_workspace
from code_agent_win.ui_composition import compose_ui
from code_agent_win.workspace_runtime import ManagedWorkspaceRuntime
from code_agent_win.workspace_context import workspace_uses_repo_map


_model_client = model_client
__all__ = ("RootActionDispatcher", "create_application")


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
    runtime_config = load_runtime_config(cli_profile=profile_name)
    unrestricted = runtime_config.approval_mode is ApprovalMode.UNRESTRICTED
    guard = WorkspacePathGuard(
        root,
        allow_sensitive=runtime_config.allow_sensitive_paths or unrestricted,
        allow_outside=runtime_config.approval_mode in {
            ApprovalMode.UNRESTRICTED,
            ApprovalMode.FULL_LOCAL,
        },
    )
    files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
    git = discover_git_workspace(root)
    repo_scanner = RepoFileScanner(files)
    repo_index = RepoIndexService(files, scan_file=repo_scanner.scan)
    repo_view_cache = RepoMapViewCache()
    repo_map_enabled = workspace_uses_repo_map(root, git_available=git is not None)
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

    sessions = SQLiteSessionRepository(_session_path())
    workspace_runtime = ManagedWorkspaceRuntime(sessions, _workspace_storage_path())
    source_services = workspace_runtime.services_for_root(root)
    guard = source_services.guard
    files = source_services.files
    git = source_services.git
    repo_index = source_services.repo_index
    approvals = ApprovalBroker()
    skills = SkillController(root, sessions, SkillApprovalAdapter(approvals))
    thread_binding = ThreadRuntimeBinding()

    context_for = RuntimeContextFactory(
        root,
        git_available=git is not None,
        repo_map_enabled=repo_map_enabled,
        guard=guard,
        files=files,
        repo_index=repo_index,
        repo_view_cache=repo_view_cache,
        sessions=sessions,
        thread_binding=thread_binding,
        skills=skills,
        workspace_runtime=workspace_runtime,
    )

    def invalidate_workspace_context(paths: tuple[str, ...]) -> None:
        repo_index.invalidate(paths)
        files.invalidate_inventory()

    (
        dispatcher,
        mcp,
        plugin_host,
        plugin_errors,
        plugin_bridge,
        interaction_broker,
    ) = compose_host(
        root=root,
        services=source_services,
        workspace_runtime=workspace_runtime,
        runtime_config=runtime_config,
        modes=modes,
        sessions=sessions,
        approvals=approvals,
        thread_binding=thread_binding,
    )

    def child_engine(agent: AgentDefinition) -> tuple[AgentEngine, object]:
        profile = configured_profiles[agent.mode.definition.profile_id]
        client = _model_client(profile.provider)
        restricted = RestrictedDispatcher(dispatcher, agent.effective_tools)
        engine = engine_for(
            client,
            profile,
            context_for(agent.mode, client, profile),
            restricted,
            sessions,
            root,
            agent.mode,
        )
        return engine, client

    subagents, plugin_names, mcp_names, plugin_mode_digests = (
        compose_subagents(
            child_engine=child_engine,
            dispatcher=dispatcher,
            sessions=sessions,
            thread_binding=thread_binding,
            modes=modes,
            profiles=configured_profiles,
            plugin_host=plugin_host,
            mode_snapshots=mode_snapshots,
        )
    )

    def main_dispatcher_for(selected: ModeSnapshot) -> RestrictedDispatcher:
        main_tools = (
            selected.definition.tool_names
            if selected.digest in plugin_mode_digests
            else main_tools_for_mode(
                selected.definition.tool_names, plugin_names
            )
            + mcp_names
        )
        return RestrictedDispatcher(dispatcher, main_tools)

    main_dispatcher = main_dispatcher_for(snapshot)
    model = _model_client(initial.provider)
    initial_runner = engine_for(
        model,
        initial,
        context_for(snapshot, model, initial),
        main_dispatcher,
        sessions,
        root,
        snapshot,
    )
    controller = AgentController(initial_runner)

    active_snapshot = snapshot
    build_snapshot = snapshot
    active_permission = runtime_config.approval_mode

    def permission_summary(mode: ApprovalMode) -> PermissionSummary:
        return PermissionSummary(
            mode,
            str(root),
            mode is ApprovalMode.UNRESTRICTED,
            mode is not ApprovalMode.PLAN,
        )

    async def build_runtime(profile: ModelProfile) -> ProviderRuntime:
        client = _model_client(profile.provider)
        runner = engine_for(
            client,
            profile,
            context_for(build_snapshot, client, profile),
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
                    permission_summary(active_permission),
                    applies_next_task=True,
                )
            )

    base_mode_control = ModeControl(
        mode_snapshots,
        snapshot.definition.mode,
        apply_mode,
    )
    plugin_mode_ids = tuple(
        item.qualified_id
        for item in plugin_host.contributions("mode")
    )
    mode_control = PluginModeControl(
        base_mode_control,
        PluginModeCatalog(plugin_host, mode_snapshots),
        plugin_mode_ids,
        apply_mode,
        plugin_mode_digests,
    )

    async def apply_permission(selected: ApprovalMode) -> None:
        nonlocal active_permission
        dispatcher.policy = ActionPolicy(
            replace(dispatcher.policy.config, approval_mode=selected)
        )
        active_permission = selected
        if tui_ref:
            tui_ref[0].update_capability(
                ModePermissionView(
                    active_snapshot,
                    permission_summary(selected),
                    applies_next_task=True,
                )
            )

    permission_control = PermissionControl(
        active_permission,
        apply_permission,
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

    foreground, tui, workflows = compose_ui(
        controller=controller,
        approvals=approvals,
        sessions=sessions,
        root=root,
        profile_supplier=profile_facts,
        profile_resolver=resolve_profile,
        subagents=subagents,
        snapshot=snapshot,
        approval_mode=runtime_config.approval_mode,
        mode_control=mode_control,
        permission_control=permission_control,
        plugin_mode_ids=plugin_mode_ids,
        plugin_host=plugin_host,
        dispatcher=dispatcher,
        interaction_broker=interaction_broker,
        skills=skills,
        mcp=mcp,
        git=git,
        checkpoints=workspace_runtime.checkpoint_control(),
        workspace_runtime=workspace_runtime,
        plugin_errors=plugin_errors,
        tui_ref=tui_ref,
    )
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
        workflows,
        workspace_runtime,
    )
    application_ref.append(application)
    return application


def _session_path() -> Path:
    directory = _product_state_root()
    base = directory.parent
    legacy = Path(base) / "code-agent" / "sessions.sqlite3"
    current = directory / "sessions.sqlite3"
    if not current.exists() and legacy.exists():
        migrate_legacy_session_database(legacy, current)
    return current


def _workspace_storage_path() -> Path:
    return _session_path().parent / "managed-workspaces"
