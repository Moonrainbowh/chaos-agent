from __future__ import annotations

import asyncio
from collections.abc import Callable
from inspect import isawaitable
from pathlib import Path

from code_agent.interfaces.command_registry import CommandRegistry, REGISTRY
from code_agent.orchestration.models import RunStatus
from code_agent.plugins.commands import PluginCommandCatalog
from code_agent.workflows.models import WorkflowNodeStatus
from code_agent.workflows.observations import (
    ChildRunObservation,
    EvidenceInvalidatedObservation,
)
from code_agent.workflows.service import WorkflowService
from code_agent_win.app_ui import ModeAwareWindowsTerminalApp
from code_agent_win.ui_runtime_composition import (
    UiComposition,
    compose_ui_runtime,
)


def compose_ui(
    *,
    controller: object,
    approvals: object,
    sessions: object,
    root: Path,
    profile_supplier: object,
    profile_resolver: object,
    runtime_resolver: object,
    runtime_selection: object,
    peers: object,
    subagents: object,
    snapshot: object,
    approval_mode: object,
    mode_control: object,
    permission_control: object,
    plugin_host: object,
    dispatcher: object,
    interaction_broker: object,
    skills: object,
    mcp: object,
    git: object,
    checkpoints: object,
    rewind: object | None,
    workspace_runtime: object,
    plugin_errors: tuple[str, ...],
    plugin_discover: Callable[[], tuple[object, object]],
    on_plugin_change: Callable[[], object],
    tui_ref: list[ModeAwareWindowsTerminalApp],
    attachment_draft: object | None = None,
    task_modes: object | None = None,
    costs: object | None = None,
    doctor: object | None = None,
    semantic_graph: object | None = None,
) -> tuple[object, ModeAwareWindowsTerminalApp, WorkflowService]:
    parts = UiComposition(
        controller, approvals, sessions, root, profile_supplier,
        profile_resolver, runtime_resolver, runtime_selection, peers, subagents,
        snapshot, approval_mode, mode_control,
        permission_control, plugin_host, dispatcher, interaction_broker, skills,
        mcp, git, checkpoints, rewind, workspace_runtime, plugin_errors,
        plugin_discover, on_plugin_change, tui_ref, plugin_command_registry,
        refresh_plugin_surfaces, _current_diff_task, _subscribe_child_workflows,
        _invalidate_workflow_verification, attachment_draft,
        task_modes, costs, doctor, semantic_graph,
    )
    return compose_ui_runtime(parts)


def plugin_command_registry(plugin_host: object) -> CommandRegistry:
    identifiers = tuple(
        item.qualified_id for item in plugin_host.contributions("mode")
    )
    return REGISTRY.with_plugin_modes(identifiers).with_plugin_commands(
        PluginCommandCatalog(plugin_host).list()
    )


async def refresh_plugin_surfaces(
    plugin_host: object,
    mode_control: object,
    tui: object,
    on_plugin_change: Callable[[], object],
) -> None:
    """Synchronize live host contributions across policy and TUI surfaces."""

    changed = on_plugin_change()
    if isawaitable(changed):
        await changed
    identifiers = tuple(
        item.qualified_id for item in plugin_host.contributions("mode")
    )
    tui.command_registry = plugin_command_registry(plugin_host)
    await mode_control.refresh(identifiers)


def _current_diff_task(tui_ref: list[ModeAwareWindowsTerminalApp]) -> str | None:
    if not tui_ref:
        return None
    return tui_ref[0].active_task_id or tui_ref[0].state.task_id


def _subscribe_child_workflows(
    subagents: object, workflows: WorkflowService
) -> None:
    statuses = {
        RunStatus.COMPLETED: WorkflowNodeStatus.COMPLETED,
        RunStatus.FAILED: WorkflowNodeStatus.FAILED,
        RunStatus.CANCELLED: WorkflowNodeStatus.CANCELLED,
    }

    def observe(view: object) -> None:
        status = statuses.get(view.status)
        thread_id = subagents.child_thread(view.run_id)
        if status is None or thread_id is None:
            return
        asyncio.create_task(
            workflows.observe(
                ChildRunObservation(
                    view.parent_run_id,
                    view.run_id,
                    thread_id,
                    view.role.value,
                    view.objective,
                    status,
                )
            )
        )

    subagents.subscribe(observe)


async def _invalidate_workflow_verification(
    sessions: object,
    workflows: WorkflowService,
    task_id: str,
    replacement_task_id: str | None,
) -> None:
    for current in (task_id, replacement_task_id):
        if current is None:
            continue
        snapshot = await sessions.load_workflow_for_task(current)
        if snapshot is None:
            continue
        for node in snapshot.nodes:
            if (
                node.kind == "verification"
                and node.status is WorkflowNodeStatus.COMPLETED
            ):
                await workflows.observe(
                    EvidenceInvalidatedObservation(current, node.id)
                )
