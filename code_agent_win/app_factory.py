from __future__ import annotations
from collections.abc import Callable
from pathlib import Path
from typing import Any
from code_agent.context.cache import RepoMapCache
from code_agent.context.repo_index import RepoIndexService
from code_agent.context.repo_map import RepoMapViewCache
from code_agent.context.repo_scan import RepoFileScanner
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.mode_control import ModeControl
from code_agent.orchestration.models import AgentMode, ModeSnapshot
from code_agent.providers.config import ModelProfile
from code_agent.providers.runtime_manager import ProviderRuntime, ProviderRuntimeManager
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.windows_paths import require_supported_windows_path
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent_win.agent_modes import (
    build_mode_registry,
    freeze_mode,
)
from code_agent_win.app_models import Application, FactoryExecution, FactoryHost
from code_agent_win.app_presentation import (
    build_application_tui,
    build_foreground,
    build_main_dispatcher,
    capability_view,
)
from code_agent_win.factory_context import build_factory_context as _context_for
from code_agent_win.factory_host import build_host_integrations
from code_agent_win.app_ui import ModeAwareWindowsTerminalApp
from code_agent_win.rewind_runtime import RewindRuntime
from code_agent_win.runtime_support import (
    profile_model_factory,
    replace_model,
)
from code_agent_win.rewind_sessions import (
    build_child_engine_factory,
    build_engine,
)
from code_agent_win.subagents import EngineChildRunner, SubagentRuntime
from code_agent_win.tool_support import discover_git_workspace
from code_agent_win.workspace_context import workspace_uses_repo_map
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
    product_state_root: Path,
) -> Application:
    if profile_name is not None and (
        not isinstance(profile_name, str) or not profile_name.strip()
    ):
        raise ValueError("profile_name must be non-blank text")
    requested_root = workspace_root or Path.cwd()
    require_supported_windows_path(
        requested_root,
        operation="workspace root",
    )
    root = requested_root.resolve()
    host = _build_host(
        root, model_name, profile_name, mode_name, load_config,
        session_path_factory, product_state_root,
    )
    execution = _build_execution(host, model_factory, context_factory)
    rewind = RewindRuntime(
        host.rewind_write.base,
        host.rewind_write.snapshots,
        host.rewind_write.capture.editor,
    )
    foreground = build_foreground(host, execution)
    application_ref: list[Application] = []
    tui_ref: list[ModeAwareWindowsTerminalApp] = []
    mode_control = _mode_control(
        host, execution, application_ref, tui_ref
    )
    tui = build_application_tui(
        host, execution, foreground, mode_control, rewind
    )
    tui_ref.append(tui)
    execution.subagents.subscribe(lambda view: tui.interactions.observe_agent(tui, view))
    application = _application(host, execution, foreground, tui, rewind)
    application_ref.append(application)
    return application


def _application(
    host: FactoryHost,
    execution: FactoryExecution,
    foreground: object,
    tui: ModeAwareWindowsTerminalApp,
    rewind: RewindRuntime,
) -> Application:
    return Application(
        controller=execution.controller,
        foreground_tasks=foreground,
        tui=tui,
        dispatcher=host.dispatcher,
        model=execution.manager,
        mcp=host.mcp,
        mode=host.mode,
        plugins=host.plugin_host,
        subagents=execution.subagents,
        rewind=rewind,
        repo_index=host.repo_index,
        attachment_store=host.attachment_store,
        attachment_ingestor=host.attachment_ingestor,
    )


def _mode_control(
    host: FactoryHost,
    execution: FactoryExecution,
    application_ref: list[Application],
    tui_ref: list[ModeAwareWindowsTerminalApp],
) -> ModeControl:
    async def apply_mode(selected: ModeSnapshot) -> None:
        previous = host.mode
        host.mode = selected
        try:
            await execution.manager.switch(
                selected.definition.profile_id, idle=True
            )
        except Exception:
            host.mode = previous
            raise
        if application_ref:
            application_ref[0].mode = selected
        if tui_ref:
            tui_ref[0].update_capability(capability_view(host, selected))

    return ModeControl(
        {mode: host.modes.freeze(mode, host.profiles) for mode in AgentMode},
        host.mode.definition.mode,
        apply_mode,
    )
def _build_host(
    root: Path,
    model_name: str | None,
    profile_name: str | None,
    mode_name: str | None,
    load_config: Callable[..., Any],
    session_path_factory: Callable[[], Path],
    product_state_root: Path,
) -> FactoryHost:
    guard = WorkspacePathGuard(root)
    files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
    git = discover_git_workspace(root)
    cache = RepoMapCache(root)
    repo_index = RepoIndexService(
        files,
        scan_file=RepoFileScanner(files).scan,
    )
    repo_view_cache = RepoMapViewCache()
    repo_map_enabled = workspace_uses_repo_map(
        root, git_available=git is not None
    )
    config = load_config(cli_profile=profile_name)
    profiles = {profile.name: profile for profile in config.profiles}
    modes, _ = build_mode_registry(profiles, config.profile)
    mode = freeze_mode(modes, profiles, mode_name)
    initial = profiles[mode.definition.profile_id]
    if model_name is not None:
        initial = replace_model(initial, model_name)
        profiles[initial.name] = initial
        mode = freeze_mode(modes, profiles, mode.definition.mode.value)
    return build_host_integrations(
        root, guard, files, git, cache, repo_index, repo_view_cache,
        repo_map_enabled, config, profiles, modes, mode, initial,
        session_path_factory, product_state_root,
    )


def _build_execution(
    host: FactoryHost,
    model_factory: Callable[[object], object],
    context_factory: Callable[..., object],
) -> FactoryExecution:
    model_factory = profile_model_factory(
        model_factory,
        host.profiles,
        getattr(host, "attachment_store", None),
    )
    child_engine = build_child_engine_factory(
        host, model_factory,
        lambda mode, sessions: _context_for(
            host, mode, context_factory, sessions),
    )
    subagents = SubagentRuntime(
        EngineChildRunner(child_engine), host.modes, host.profiles,
    )
    host.dispatcher.subagents = subagents
    main_dispatcher = build_main_dispatcher(host, host.mode)
    model = model_factory(host.initial.provider)
    runner = build_engine(
        model, host.initial, _context_for(host, host.mode, context_factory), main_dispatcher,
        host.sessions, host.root, host.mode,
    )
    controller = AgentController(runner)

    async def build_runtime(profile: ModelProfile) -> ProviderRuntime:
        client = model_factory(profile.provider)
        selected_dispatcher = build_main_dispatcher(host, host.mode)
        next_runner = build_engine(
            client, profile, _context_for(host, host.mode, context_factory),
            selected_dispatcher,
            host.sessions, host.root, host.mode,
        )
        return ProviderRuntime(profile, client, next_runner)

    manager = ProviderRuntimeManager(
        host.profiles, ProviderRuntime(host.initial, model, runner),
        build_runtime, controller.replace_runner,
    )
    return FactoryExecution(subagents, main_dispatcher, manager, controller)


__all__ = ["create_application"]
