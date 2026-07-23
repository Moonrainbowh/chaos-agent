from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from code_agent.core.engine import AgentEngine
from code_agent.interfaces.capability_view import ModePermissionView, PermissionSummary
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.mode_control import ModeControl
from code_agent.interfaces.permission_control import PermissionControl
from code_agent.orchestration.models import AgentDefinition, AgentMode, ModeSnapshot
from code_agent.orchestration.plugin_extensions import PluginModeCatalog
from code_agent.policy.engine import ActionPolicy
from code_agent.policy.models import ApprovalMode
from code_agent.providers.config import ModelProfile
from code_agent.providers.runtime_manager import ProviderRuntime, ProviderRuntimeManager
from code_agent_win.agent_modes import main_tools_for_mode
from code_agent_win.app_ui import ModeAwareWindowsTerminalApp, PluginModeControl
from code_agent_win.application_context import engine_for
from code_agent_win.host_composition import compose_subagents
from code_agent_win.subagents import RestrictedDispatcher, SubagentRuntime


@dataclass(frozen=True)
class RuntimeControls:
    controller: AgentController
    manager: ProviderRuntimeManager
    subagents: SubagentRuntime
    mode_control: PluginModeControl
    permission_control: PermissionControl
    profile_facts: Callable[[], tuple[str, str, str, str]]
    profile_resolver: Callable[[str], object]


class _DispatcherFactory:
    def __init__(
        self,
        *,
        root: Path,
        profiles: Mapping[str, ModelProfile],
        client_factory: Callable[[object], object],
        context_for: Callable[[ModeSnapshot, object, ModelProfile], object],
        dispatcher: object,
        sessions: object,
        plugin_bridge: object,
        plugin_bindings: object,
    ) -> None:
        self._root, self._profiles = root, profiles
        self._client_factory, self._context_for = client_factory, context_for
        self._dispatcher, self._sessions = dispatcher, sessions
        self._plugin_bridge, self._plugin_bindings = plugin_bridge, plugin_bindings
        self._mcp_names: tuple[str, ...] = ()
        self.plugin_mode_digests: set[str] = set()

    def child_engine(self, agent: AgentDefinition) -> tuple[AgentEngine, object]:
        profile = self._profiles[agent.mode.definition.profile_id]
        client = self._client_factory(profile.provider)
        engine = engine_for(
            client,
            profile,
            self._context_for(agent.mode, client, profile),
            RestrictedDispatcher(self._dispatcher, agent.effective_tools),
            self._sessions,
            self._root,
            agent.mode,
        )
        return engine, client

    def attach_subagents(
        self,
        *,
        thread_binding: object,
        modes: object,
        plugin_host: object,
        mode_snapshots: Mapping[AgentMode, ModeSnapshot],
    ) -> SubagentRuntime:
        subagents, self._mcp_names, self.plugin_mode_digests = compose_subagents(
            child_engine=self.child_engine,
            dispatcher=self._dispatcher,
            sessions=self._sessions,
            thread_binding=thread_binding,
            modes=modes,
            profiles=self._profiles,
            plugin_host=plugin_host,
            mode_snapshots=mode_snapshots,
        )
        return subagents

    def __call__(self, selected: ModeSnapshot) -> RestrictedDispatcher:
        restricted = RestrictedDispatcher(self._dispatcher, self._tools(selected))
        return self._plugin_bindings.bind(
            restricted,
            lambda selected=selected: self._tools(selected),
        )

    def _tools(self, selected: ModeSnapshot) -> tuple[str, ...]:
        if selected.digest in self.plugin_mode_digests:
            return selected.definition.tool_names
        plugin_names = tuple(tool.name for tool in self._plugin_bridge.definitions())
        return main_tools_for_mode(selected.definition.tool_names, plugin_names) + self._mcp_names


class _ProviderControls:
    def __init__(
        self,
        *,
        root: Path,
        snapshot: ModeSnapshot,
        mode_snapshots: Mapping[AgentMode, ModeSnapshot],
        profiles: Mapping[str, ModelProfile],
        initial: ModelProfile,
        model: object,
        initial_runner: AgentEngine,
        controller: AgentController,
        client_factory: Callable[[object], object],
        context_for: Callable[[ModeSnapshot, object, ModelProfile], object],
        dispatcher_factory: _DispatcherFactory,
        dispatcher: object,
        sessions: object,
        approval_mode: ApprovalMode,
        plugin_host: object,
        application_ref: list[object],
        tui_ref: list[ModeAwareWindowsTerminalApp],
    ) -> None:
        self._root, self._profiles = root, profiles
        self._active_snapshot = self._build_snapshot = snapshot
        self._active_permission = approval_mode
        self._client_factory, self._context_for = client_factory, context_for
        self._dispatcher_factory = dispatcher_factory
        self._dispatcher, self._sessions = dispatcher, sessions
        self._application_ref, self._tui_ref = application_ref, tui_ref
        self.manager = ProviderRuntimeManager(
            profiles,
            ProviderRuntime(initial, model, initial_runner),
            self._build_runtime,
            controller.replace_runner,
        )
        base_mode = ModeControl(mode_snapshots, snapshot.definition.mode, self._apply_mode)
        identifiers = tuple(item.qualified_id for item in plugin_host.contributions("mode"))
        self.mode_control = PluginModeControl(
            base_mode,
            PluginModeCatalog(plugin_host, mode_snapshots),
            identifiers,
            self._apply_mode,
            dispatcher_factory.plugin_mode_digests,
        )
        self.permission_control = PermissionControl(approval_mode, self._apply_permission)

    async def _build_runtime(self, profile: ModelProfile) -> ProviderRuntime:
        client = self._client_factory(profile.provider)
        runner = engine_for(
            client,
            profile,
            self._context_for(self._build_snapshot, client, profile),
            self._dispatcher_factory(self._build_snapshot),
            self._sessions,
            self._root,
            self._build_snapshot,
        )
        return ProviderRuntime(profile, client, runner)

    async def _apply_mode(self, selected: ModeSnapshot) -> None:
        previous = self._active_snapshot
        self._build_snapshot = selected
        try:
            await self.manager.switch(selected.definition.profile_id, idle=True)
        except Exception:
            self._build_snapshot = previous
            raise
        self._active_snapshot = selected
        if self._application_ref:
            self._application_ref[0].mode = selected
        self._update_capability(selected, self._active_permission)

    async def _apply_permission(self, selected: ApprovalMode) -> None:
        self._dispatcher.policy = ActionPolicy(
            replace(self._dispatcher.policy.config, approval_mode=selected)
        )
        self._active_permission = selected
        self._update_capability(self._active_snapshot, selected)

    def _update_capability(
        self, snapshot: ModeSnapshot, permission: ApprovalMode
    ) -> None:
        if self._tui_ref:
            self._tui_ref[0].update_capability(
                ModePermissionView(
                    snapshot,
                    _permission_summary(self._root, permission),
                    applies_next_task=True,
                )
            )

    def profile_facts(self) -> tuple[str, str, str, str]:
        profile = self.manager.current.profile
        return (
            profile.name,
            profile.provider.model,
            profile.provider.api.value,
            urlsplit(profile.provider.base_url).hostname or "unknown",
        )

    async def resolve_profile(self, name: str) -> None:
        if name != self._active_snapshot.definition.profile_id:
            raise RuntimeError("recorded task mode profile is unavailable")
        if self.manager.current.profile.name != name:
            await self.manager.switch(name, idle=True)


def compose_runtime_controls(
    *,
    root: Path, snapshot: ModeSnapshot,
    mode_snapshots: Mapping[AgentMode, ModeSnapshot], profiles: Mapping[str, ModelProfile],
    modes: object, initial: ModelProfile,
    client_factory: Callable[[object], object],
    context_for: Callable[[ModeSnapshot, object, ModelProfile], object],
    dispatcher: object, sessions: object, thread_binding: object,
    plugin_host: object, plugin_bridge: object, plugin_bindings: object,
    approval_mode: ApprovalMode, application_ref: list[object],
    tui_ref: list[ModeAwareWindowsTerminalApp],
) -> RuntimeControls:
    factory = _DispatcherFactory(
        root=root, profiles=profiles, client_factory=client_factory,
        context_for=context_for, dispatcher=dispatcher, sessions=sessions,
        plugin_bridge=plugin_bridge, plugin_bindings=plugin_bindings,
    )
    subagents = factory.attach_subagents(
        thread_binding=thread_binding, modes=modes,
        plugin_host=plugin_host, mode_snapshots=mode_snapshots,
    )
    main_dispatcher = factory(snapshot)
    model = client_factory(initial.provider)
    runner = engine_for(
        model, initial, context_for(snapshot, model, initial),
        main_dispatcher, sessions, root, snapshot,
    )
    controller = AgentController(runner)
    controls = _ProviderControls(
        root=root, snapshot=snapshot, mode_snapshots=mode_snapshots,
        profiles=profiles, initial=initial, model=model,
        initial_runner=runner, controller=controller,
        client_factory=client_factory, context_for=context_for,
        dispatcher_factory=factory, dispatcher=dispatcher, sessions=sessions,
        approval_mode=approval_mode, plugin_host=plugin_host,
        application_ref=application_ref, tui_ref=tui_ref,
    )
    return RuntimeControls(
        controller, controls.manager, subagents, controls.mode_control,
        controls.permission_control, controls.profile_facts, controls.resolve_profile,
    )


def _permission_summary(root: Path, mode: ApprovalMode) -> PermissionSummary:
    return PermissionSummary(
        mode,
        str(root),
        mode is ApprovalMode.UNRESTRICTED,
        mode is not ApprovalMode.PLAN,
    )
