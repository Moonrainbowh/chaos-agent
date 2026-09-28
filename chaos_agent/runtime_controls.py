from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from code_agent.capabilities import CapabilityStrategy
from code_agent.core.engine import AgentEngine
from code_agent.core.task import TaskContract
from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.permission_control import PermissionControl
from code_agent.orchestration.models import AgentMode, AgentTopology, ModeSnapshot
from code_agent.orchestration.modes import (
    attach_runtime_selection,
    freeze_runtime_selection,
)
from code_agent.policy.models import ApprovalMode
from code_agent.providers.config import ModelProfile
from code_agent.providers.runtime_manager import ProviderRuntimeManager
from code_agent_win.agent_modes import runtime_effort_for_mode
from code_agent_win.app_ui import ModeAwareWindowsTerminalApp, PluginModeControl
from code_agent_win.application_context import engine_for
from code_agent_win.runtime_dispatcher_factory import RuntimeDispatcherFactory
from code_agent_win.runtime_provider_controls import ProviderControls
from code_agent_win.runtime_selection_control import (
    RuntimeSelectionControl,
    RuntimeSelectionSummary,
    validate_profile_reasoning,
)
from code_agent_win.subagents import SubagentRuntime


__all__ = (
    "RuntimeControls",
    "RuntimeSelectionControl",
    "RuntimeSelectionSummary",
    "compose_runtime_controls",
)


@dataclass(frozen=True)
class RuntimeControls:
    controller: AgentController
    manager: ProviderRuntimeManager
    subagents: SubagentRuntime
    mode_control: PluginModeControl
    permission_control: PermissionControl
    profile_facts: Callable[[], tuple[str, ...]]
    profile_resolver: Callable[[str], Awaitable[None]]
    runtime_selection: RuntimeSelectionControl
    runtime_resolver: Callable[[TaskContract], Awaitable[None]]
    register_profile: Callable[[ModelProfile], None] | None = None
    set_profile_restorer: Callable[[Callable[[str], Awaitable[None]]], None] | None = None


def compose_runtime_controls(
    *,
    root: Path, snapshot: ModeSnapshot,
    mode_snapshots: Mapping[AgentMode, ModeSnapshot], profiles: Mapping[str, ModelProfile],
    modes: object, initial: ModelProfile,
    client_factory: Callable[..., object],
    context_for: Callable[[ModeSnapshot, object, ModelProfile], object],
    dispatcher: object, sessions: object, thread_binding: object,
    plugin_host: object, plugin_bridge: object, plugin_bindings: object,
    approval_mode: ApprovalMode, application_ref: list[object],
    tui_ref: list[ModeAwareWindowsTerminalApp],
    capability_strategy: CapabilityStrategy = CapabilityStrategy.HYBRID,
    context_wrapper: Callable[[object], object] | None = None,
    activity_lock: asyncio.Lock | None = None,
) -> RuntimeControls:
    snapshot = _ensure_runtime_selection(snapshot, profiles)
    initial = profiles[snapshot.profile_id]
    factory = RuntimeDispatcherFactory(
        root=root, profiles=profiles, client_factory=client_factory,
        context_for=context_for, dispatcher=dispatcher, sessions=sessions,
        plugin_bridge=plugin_bridge, plugin_bindings=plugin_bindings,
        capability_strategy=capability_strategy,
    )
    subagents = factory.attach_subagents(
        thread_binding=thread_binding, modes=modes,
        plugin_host=plugin_host, mode_snapshots=mode_snapshots,
    )
    controller, model, runner = _initial_runtime(
        root, snapshot, initial, client_factory, context_for,
        factory, sessions, context_wrapper,
    )
    controls = ProviderControls(
        root=root, snapshot=snapshot, mode_snapshots=mode_snapshots,
        profiles=profiles, initial=initial, model=model,
        initial_runner=runner, controller=controller,
        client_factory=client_factory, context_for=context_for,
        dispatcher_factory=factory, dispatcher=dispatcher, sessions=sessions,
        approval_mode=approval_mode, plugin_host=plugin_host,
        application_ref=application_ref, tui_ref=tui_ref,
        context_wrapper=context_wrapper, activity_lock=activity_lock,
    )
    return RuntimeControls(
        controller, controls.manager, subagents, controls.mode_control,
        controls.permission_control, controls.profile_facts,
        controls.resolve_profile, controls.runtime_selection,
        controls.resolve_runtime_contract,
        controls.register_profile,
        controls.set_profile_restorer,
    )


def _ensure_runtime_selection(
    snapshot: ModeSnapshot, profiles: Mapping[str, ModelProfile]
) -> ModeSnapshot:
    if snapshot.runtime_selection is not None:
        return snapshot
    selection = freeze_runtime_selection(
        profiles,
        profile_id=snapshot.definition.profile_id,
        topology=AgentTopology.SINGLE,
        reasoning_effort=runtime_effort_for_mode(
            snapshot.definition.reasoning_effort
        ),
        legacy_mode=snapshot.definition.mode,
    )
    validate_profile_reasoning(
        profiles[selection.profile_id], selection.reasoning_effort
    )
    return attach_runtime_selection(snapshot, selection)


def _initial_runtime(
    root: Path, snapshot: ModeSnapshot, initial: ModelProfile,
    client_factory: Callable[..., object],
    context_for: Callable[[ModeSnapshot, object, ModelProfile], object],
    factory: RuntimeDispatcherFactory, sessions: object,
    context_wrapper: Callable[[object], object] | None,
) -> tuple[AgentController, object, AgentEngine]:
    model = client_factory(
        initial.provider,
        reasoning_effort=snapshot.effective_reasoning_effort,
    )
    context = context_for(snapshot, model, initial)
    if context_wrapper is not None:
        context = context_wrapper(context)
    runner = engine_for(
        model, initial, context, factory(snapshot), sessions, root, snapshot,
        capability_strategy=factory.capability_strategy,
    )
    return AgentController(runner), model, runner
