from __future__ import annotations

from typing import Any

from code_agent.interfaces.cost_control import TaskCostControl
from code_agent.interfaces.task_mode_control import TaskModeControl
from code_agent_win.multimodal_ui import build_attachment_draft
from code_agent_win.runtime_controls import compose_runtime_controls
from code_agent_win.semantic_insights import SemanticGraphControl
from code_agent_win.system_diagnostics import SystemDoctor
from code_agent_win.ui_composition import compose_ui


def configure_product_controls(host: Any) -> None:
    """Compose runtime selection and read-only user controls."""
    host.application_ref = []
    host.controls = compose_runtime_controls(
        root=host.root, snapshot=host.snapshot,
        mode_snapshots=host.mode_snapshots, profiles=host.profiles,
        modes=host.modes, initial=host.initial,
        client_factory=host.model_factory,
        context_for=host.context_for, dispatcher=host.dispatcher,
        sessions=host.sessions, thread_binding=host.thread_binding,
        plugin_host=host.plugin_host, plugin_bridge=host.plugin_bridge,
        plugin_bindings=host.plugin_bindings,
        approval_mode=host.runtime_config.approval_mode,
        capability_strategy=host.runtime_config.capability_strategy,
        application_ref=host.application_ref, tui_ref=host.tui_ref,
        context_wrapper=host.peers.wrap_context,
        activity_lock=host.activity_lock,
    )
    host.snapshot = host.controls.runtime_selection.snapshot
    host.task_modes = TaskModeControl()
    host.costs = TaskCostControl(host.sessions, host.profiles)
    host.semantic_graph = SemanticGraphControl(host.root, host.workspace_runtime)
    host.doctor = SystemDoctor(
        host.root,
        powershell=host.powershell,
        git=host.git,
        base_url=lambda: host.profiles[
            host.controls.runtime_selection.current.profile
        ].provider.base_url,
    )


def configure_product_ui(host: Any) -> None:
    """Wire foreground tasks and the terminal product surface."""
    host.foreground, host.tui, host.workflows = compose_ui(
        controller=host.controls.controller,
        approvals=host.approvals,
        sessions=host.sessions,
        root=host.root,
        profile_supplier=host.controls.profile_facts,
        profile_resolver=host.controls.profile_resolver,
        runtime_resolver=host.controls.runtime_resolver,
        runtime_selection=host.controls.runtime_selection,
        peers=host.peers,
        subagents=host.controls.subagents,
        snapshot=host.snapshot,
        approval_mode=host.runtime_config.approval_mode,
        mode_control=host.controls.mode_control,
        permission_control=host.controls.permission_control,
        plugin_host=host.plugin_host,
        dispatcher=host.dispatcher,
        interaction_broker=host.interaction_broker,
        skills=host.skills,
        mcp=host.mcp,
        git=host.git,
        checkpoints=host.workspace_runtime.checkpoint_control(),
        rewind=host.rewind,
        workspace_runtime=host.workspace_runtime,
        plugin_errors=host.plugin_errors,
        plugin_discover=host.plugin_discover,
        on_plugin_change=host.plugin_bindings.refresh,
        tui_ref=host.tui_ref,
        attachment_draft=build_attachment_draft(
            host.attachment_ingestor,
            lambda: host.controls.manager.current.profile,
        ),
        task_modes=host.task_modes,
        costs=host.costs,
        doctor=host.doctor,
        semantic_graph=host.semantic_graph,
    )
