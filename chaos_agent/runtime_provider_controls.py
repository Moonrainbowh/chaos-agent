from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from code_agent.core.engine import AgentEngine
from code_agent.core.task import TaskContract
from code_agent.interfaces.capability_view import ModePermissionView, PermissionSummary
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.mode_control import ModeControl
from code_agent.interfaces.permission_control import PermissionControl
from code_agent.orchestration.models import AgentMode, ModeSnapshot
from code_agent.orchestration.modes import (
    attach_runtime_selection,
    freeze_runtime_selection,
)
from code_agent.orchestration.plugin_extensions import PluginModeCatalog
from code_agent.policy.engine import ActionPolicy
from code_agent.policy.models import ApprovalMode
from code_agent.providers.config import ModelProfile
from code_agent.providers.runtime_manager import ProviderRuntime, ProviderRuntimeManager
from code_agent_win.app_ui import ModeAwareWindowsTerminalApp, PluginModeControl
from code_agent_win.application_context import engine_for
from code_agent_win.runtime_dispatcher_factory import RuntimeDispatcherFactory
from code_agent_win.runtime_client_cleanup import close_partial_client
from code_agent_win.runtime_selection_control import (
    RuntimeSelectionControl,
    validate_profile_reasoning,
)


class ProviderControls:
    def set_profile_restorer(self, restore) -> None:
        self._profile_restorer = restore

    def register_profile(self, profile: ModelProfile) -> None:
        """Register in all runtime views without switching or writing config."""
        if not isinstance(profile, ModelProfile):
            raise TypeError("profile must be a ModelProfile")
        previous = self._profiles.get(profile.name)
        if previous is not None and previous != profile:
            raise ValueError("profile name already belongs to another configuration")
        self.manager.register_profile(profile)
        self.runtime_selection.register_profile(profile)
        self._profiles[profile.name] = profile

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
        client_factory: Callable[..., object],
        context_for: Callable[[ModeSnapshot, object, ModelProfile], object],
        dispatcher_factory: RuntimeDispatcherFactory,
        dispatcher: object,
        sessions: object,
        approval_mode: ApprovalMode,
        plugin_host: object,
        application_ref: list[object],
        tui_ref: list[ModeAwareWindowsTerminalApp],
        context_wrapper: Callable[[object], object] | None,
        activity_lock: asyncio.Lock | None,
    ) -> None:
        self._bind_runtime(
            root, snapshot, mode_snapshots, profiles, client_factory,
            context_for, dispatcher_factory, dispatcher, sessions,
            application_ref, tui_ref, context_wrapper, activity_lock,
        )
        self.manager = ProviderRuntimeManager(
            profiles,
            ProviderRuntime(initial, model, initial_runner),
            self._build_runtime,
            controller.replace_runner,
        )
        self._configure_controls(mode_snapshots, snapshot, plugin_host, approval_mode)

    def _bind_runtime(
        self, root: Path, snapshot: ModeSnapshot,
        mode_snapshots: Mapping[AgentMode, ModeSnapshot],
        profiles: Mapping[str, ModelProfile], client_factory: Callable[..., object],
        context_for: Callable[[ModeSnapshot, object, ModelProfile], object],
        dispatcher_factory: RuntimeDispatcherFactory, dispatcher: object,
        sessions: object, application_ref: list[object],
        tui_ref: list[ModeAwareWindowsTerminalApp],
        context_wrapper: Callable[[object], object] | None,
        activity_lock: asyncio.Lock | None,
    ) -> None:
        self._root, self._profiles = root, profiles
        self._mode_snapshots = dict(mode_snapshots)
        self._active_snapshot = self._build_snapshot = snapshot
        self._client_factory, self._context_for = client_factory, context_for
        self._context_wrapper = context_wrapper
        self._dispatcher_factory = dispatcher_factory
        self._dispatcher, self._sessions = dispatcher, sessions
        self._application_ref, self._tui_ref = application_ref, tui_ref
        self._switch_lock = activity_lock or asyncio.Lock()

    def _configure_controls(
        self, mode_snapshots: Mapping[AgentMode, ModeSnapshot],
        snapshot: ModeSnapshot, plugin_host: object, approval_mode: ApprovalMode,
    ) -> None:
        self._active_permission = approval_mode
        base = ModeControl(mode_snapshots, snapshot.definition.mode, self._apply_mode)
        identifiers = tuple(
            item.qualified_id for item in plugin_host.contributions("mode")
        )
        self.mode_control = PluginModeControl(
            base,
            PluginModeCatalog(plugin_host, mode_snapshots),
            identifiers,
            self._apply_mode,
            self._dispatcher_factory.plugin_mode_digests,
        )
        self.permission_control = PermissionControl(
            approval_mode,
            self._apply_permission,
            rules=getattr(self._dispatcher, "process_rules", None),
            workspace_root=self._root,
            workspace_fingerprint=getattr(
                self._dispatcher, "workspace_fingerprint", None
            ),
        )
        self.runtime_selection = RuntimeSelectionControl(
            self._profiles,
            snapshot,
            self._apply_runtime_selection,
            lambda: self._active_snapshot,
        )

    async def _build_runtime(self, profile: ModelProfile) -> ProviderRuntime:
        client = self._client_factory(
            profile.provider,
            reasoning_effort=self._build_snapshot.effective_reasoning_effort,
        )
        try:
            context = self._context_for(self._build_snapshot, client, profile)
            if self._context_wrapper is not None:
                context = self._context_wrapper(context)
            runner = engine_for(
                client, profile, context,
                self._dispatcher_factory(self._build_snapshot),
                self._sessions, self._root, self._build_snapshot,
                capability_strategy=self._dispatcher_factory.capability_strategy,
            )
            return ProviderRuntime(profile, client, runner)
        except BaseException:
            await close_partial_client(client)
            raise

    async def _apply_mode(self, selected: ModeSnapshot) -> None:
        active = self._active_snapshot.runtime_selection
        if active is None:
            raise RuntimeError("runtime selection is unavailable")
        selection = freeze_runtime_selection(
            self._profiles,
            profile_id=active.profile_id,
            topology=active.topology,
            reasoning_effort=active.reasoning_effort,
            legacy_mode=selected.definition.mode,
        )
        effective = attach_runtime_selection(selected, selection)
        await self._replace_runtime(effective)
        self.runtime_selection.observe(effective)

    async def _apply_runtime_selection(self, selected: ModeSnapshot) -> None:
        await self._replace_runtime(selected)

    async def _replace_runtime(self, selected: ModeSnapshot) -> None:
        async with self._switch_lock:
            previous = self._active_snapshot
            self._build_snapshot = selected
            try:
                await self.manager.switch(selected.profile_id, idle=True)
            except BaseException:
                self._build_snapshot = previous
                raise
            self._active_snapshot = selected
            if self._application_ref:
                self._application_ref[0].mode = selected
            self._update_capability(selected, self._active_permission)

    async def _apply_permission(self, selected: ApprovalMode) -> None:
        async with self._switch_lock:
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

    def profile_facts(self) -> tuple[str, ...]:
        profile = self.manager.current.profile
        selection = self._active_snapshot.runtime_selection
        if selection is None:
            raise RuntimeError("runtime selection is unavailable")
        return (
            profile.name,
            profile.provider.model,
            profile.provider.api.value,
            _endpoint_host(profile),
            selection.topology.value,
            selection.reasoning_effort.value,
            selection.legacy_mode.value,
            self._active_snapshot.digest,
        )

    async def resolve_profile(self, name: str) -> None:
        async with self._switch_lock:
            if name != self._active_snapshot.profile_id:
                raise RuntimeError("recorded task mode profile is unavailable")
            if self.manager.current.profile.name != name:
                await self.manager.switch(name, idle=True)

    async def resolve_runtime_contract(self, contract: TaskContract) -> None:
        restore = getattr(self, "_profile_restorer", None)
        if restore is not None:
            await restore(contract.profile_id)
        facts = _contract_facts(contract)
        if not all(isinstance(value, str) and value.strip() for value in facts):
            raise ValueError("recorded runtime selection is incomplete")
        mode = AgentMode(contract.runtime_mode)
        selection = freeze_runtime_selection(
            self._profiles,
            profile_id=contract.profile_id,
            topology=contract.agent_topology,
            reasoning_effort=contract.reasoning_effort,
            legacy_mode=mode,
        )
        profile = self._profiles[selection.profile_id]
        validate_profile_reasoning(profile, selection.reasoning_effort)
        if _profile_identity(profile) != (
            contract.model, contract.protocol, contract.endpoint_host
        ):
            raise RuntimeError("recorded runtime selection no longer matches configuration")
        active = self._active_snapshot
        if _active_matches(active, selection.digest, contract.runtime_selection_digest):
            return
        snapshot = attach_runtime_selection(self._mode_snapshots[mode], selection)
        if snapshot.digest != contract.runtime_selection_digest:
            raise RuntimeError("recorded runtime mode identity is unavailable")
        await self._replace_runtime(snapshot)
        self.runtime_selection.observe(snapshot)

def _contract_facts(contract: TaskContract) -> tuple[str | None, ...]:
    return (
        contract.profile_id, contract.model, contract.protocol,
        contract.endpoint_host, contract.agent_topology,
        contract.reasoning_effort, contract.runtime_mode,
        contract.runtime_selection_digest,
    )

def _active_matches(
    active: ModeSnapshot, selection_digest: str, contract_digest: str | None
) -> bool:
    selection = active.runtime_selection
    return (
        active.digest == contract_digest
        and selection is not None
        and selection.digest == selection_digest
    )

def _profile_identity(profile: ModelProfile) -> tuple[str, str, str]:
    return (
        profile.provider.model,
        profile.provider.api.value,
        _endpoint_host(profile),
    )

def _endpoint_host(profile: ModelProfile) -> str:
    return urlsplit(profile.provider.base_url).hostname or "unknown"


def _permission_summary(root: Path, mode: ApprovalMode) -> PermissionSummary:
    return PermissionSummary(
        mode,
        str(root),
        mode is ApprovalMode.UNRESTRICTED,
        mode is not ApprovalMode.PLAN,
    )
