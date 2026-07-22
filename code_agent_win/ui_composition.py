from __future__ import annotations

import asyncio
from pathlib import Path

from code_agent.interfaces.capability_view import (
    ModePermissionView,
    PermissionSummary,
)
from code_agent.interfaces.command_registry import REGISTRY
from code_agent.interfaces.interaction import PluginInteractionAdapter
from code_agent.interfaces.terminal_display import DisplayKind
from code_agent.orchestration.models import RunStatus
from code_agent.policy.models import ApprovalMode
from code_agent.plugins.commands import PluginCommandCatalog
from code_agent.workflows.models import WorkflowNodeStatus
from code_agent.workflows.observations import (
    ChildRunObservation,
    EvidenceInvalidatedObservation,
)
from code_agent.workflows.service import WorkflowService
from code_agent_win.app_ui import (
    GitDiffAdapter,
    ModeAwareWindowsTerminalApp,
    TaskScopedGitDiffAdapter,
)
from code_agent_win.foreground_tasks import IntegratedForegroundTaskController
from code_agent_win.plugin_runtime import (
    PluginCommandController,
    PluginEventCoordinator,
)


def compose_ui(
    *,
    controller: object,
    approvals: object,
    sessions: object,
    root: Path,
    profile_supplier: object,
    profile_resolver: object,
    subagents: object,
    snapshot: object,
    approval_mode: object,
    mode_control: object,
    permission_control: object,
    plugin_mode_ids: tuple[str, ...],
    plugin_host: object,
    dispatcher: object,
    interaction_broker: object,
    skills: object,
    mcp: object,
    git: object,
    checkpoints: object,
    workspace_runtime: object,
    plugin_errors: tuple[str, ...],
    tui_ref: list[ModeAwareWindowsTerminalApp],
) -> tuple[object, ModeAwareWindowsTerminalApp, WorkflowService]:
    pending_notifications: list[object] = []

    def display_plugin_notification(interaction: object) -> None:
        if tui_ref:
            tui_ref[0]._append(DisplayKind.METADATA, interaction.prompt)
        else:
            pending_notifications.append(interaction)

    plugin_events = PluginEventCoordinator(
        plugin_host,
        PluginInteractionAdapter(
            interaction_broker, display_plugin_notification
        ),
        dispatcher,
    )
    workflows = WorkflowService(sessions)
    workspace_runtime.set_verification_invalidator(
        lambda task_id, replacement: _invalidate_workflow_verification(
            sessions, workflows, task_id, replacement
        )
    )
    foreground = IntegratedForegroundTaskController(
        controller,
        sessions,
        root,
        profile_supplier=profile_supplier,
        profile_resolver=profile_resolver,
        subagents=subagents,
        workflows=workflows,
        plugin_events=plugin_events,
        workspace_runtime=workspace_runtime,
    )
    command_registry = REGISTRY.with_plugin_modes(
        plugin_mode_ids
    ).with_plugin_commands(PluginCommandCatalog(plugin_host).list())
    plugin_commands = PluginCommandController(plugin_host)
    tui = ModeAwareWindowsTerminalApp(
        controller,
        approvals,
        sessions=sessions,
        evidence=sessions,
        history=sessions,
        tasks=foreground,
        modes=mode_control,
        permissions=permission_control,
        skills=skills,
        mcp=mcp,
        workflows=sessions,
        command_registry=command_registry,
        checkpoints=checkpoints,
        plugins=plugin_commands,
        interaction_broker=interaction_broker,
        diff_source=TaskScopedGitDiffAdapter(
            workspace_runtime,
            lambda: _current_diff_task(tui_ref),
            GitDiffAdapter(git),
        ),
        capability=ModePermissionView(
            snapshot,
            PermissionSummary(
                approval_mode,
                str(root),
                approval_mode is ApprovalMode.UNRESTRICTED,
                approval_mode is not ApprovalMode.PLAN,
            ),
        ),
        recover_pending=workspace_runtime.recover_pending,
        plugin_errors=plugin_errors,
    )
    tui_ref.append(tui)
    for notification in pending_notifications:
        display_plugin_notification(notification)
    plugin_commands.attach(tui)
    subagents.subscribe(
        lambda view: tui.interactions.observe_agent(tui, view)
    )
    _subscribe_child_workflows(subagents, workflows)
    return foreground, tui, workflows


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
