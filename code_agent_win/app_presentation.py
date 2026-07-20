from __future__ import annotations

from urllib.parse import urlsplit

from code_agent.interfaces.capability_view import (
    ModePermissionView,
    PermissionSummary,
)
from code_agent.interfaces.mode_control import ModeControl
from code_agent.orchestration.models import ModeSnapshot

from code_agent_win.agent_modes import main_tools_for_mode
from code_agent_win.app_models import FactoryExecution, FactoryHost
from code_agent_win.app_ui import (
    GitDiffAdapter,
    IntegratedForegroundTaskController,
    ModeAwareWindowsTerminalApp,
)
from code_agent_win.rewind_runtime import RewindRuntime
from code_agent_win.subagents import RestrictedDispatcher


def build_main_dispatcher(
    host: FactoryHost, mode: ModeSnapshot
) -> RestrictedDispatcher:
    plugin_names = tuple(
        tool.name for tool in host.plugin_bridge.definitions()
    )
    tools = main_tools_for_mode(mode.definition.tool_names, plugin_names)
    return RestrictedDispatcher(host.dispatcher, tools)


def build_foreground(
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


def build_application_tui(
    host: FactoryHost,
    execution: FactoryExecution,
    foreground: IntegratedForegroundTaskController,
    modes: ModeControl,
    rewind: RewindRuntime,
) -> ModeAwareWindowsTerminalApp:
    return ModeAwareWindowsTerminalApp(
        execution.controller,
        host.approvals,
        sessions=host.sessions,
        evidence=host.sessions,
        history=host.sessions,
        tasks=foreground,
        modes=modes,
        skills=host.skills,
        mcp=host.mcp,
        diff_source=GitDiffAdapter(host.git),
        capability=capability_view(host, host.mode),
        plugin_errors=host.plugin_errors,
        rewind=rewind,
    )


def capability_view(
    host: FactoryHost, mode: ModeSnapshot
) -> ModePermissionView:
    return ModePermissionView(
        mode,
        PermissionSummary(
            host.runtime_config.approval_mode,
            str(host.root),
            False,
            True,
        ),
        applies_next_task=True,
    )


__all__ = [
    "build_application_tui",
    "build_foreground",
    "build_main_dispatcher",
    "capability_view",
]
