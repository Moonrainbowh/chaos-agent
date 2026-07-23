from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from code_agent.interfaces.capability_view import ModePermissionView, PermissionSummary
from code_agent.interfaces.interaction import PluginInteractionAdapter
from code_agent.interfaces.terminal_display import DisplayKind
from code_agent.policy.models import ApprovalMode
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


@dataclass(frozen=True)
class UiComposition:
    controller: object
    approvals: object
    sessions: object
    root: Path
    profile_supplier: object
    profile_resolver: object
    subagents: object
    snapshot: object
    approval_mode: object
    mode_control: object
    permission_control: object
    plugin_host: object
    dispatcher: object
    interaction_broker: object
    skills: object
    mcp: object
    git: object
    checkpoints: object
    rewind: object | None
    workspace_runtime: object
    plugin_errors: tuple[str, ...]
    plugin_discover: Callable[[], tuple[object, object]]
    on_plugin_change: Callable[[], object]
    tui_ref: list[ModeAwareWindowsTerminalApp]
    plugin_registry: Callable[[object], object]
    surface_refresher: Callable[..., object]
    current_diff_task: Callable[[list[ModeAwareWindowsTerminalApp]], str | None]
    workflow_subscriber: Callable[[object, WorkflowService], None]
    verification_invalidator: Callable[..., object]


def compose_ui_runtime(
    parts: UiComposition,
) -> tuple[object, ModeAwareWindowsTerminalApp, WorkflowService]:
    return _UiComposer(parts).compose()


class _UiComposer:
    def __init__(self, parts: UiComposition) -> None:
        self.parts = parts
        self.pending_notifications: list[object] = []

    def compose(
        self,
    ) -> tuple[object, ModeAwareWindowsTerminalApp, WorkflowService]:
        self._compose_plugins()
        self._compose_foreground()
        self._compose_tui()
        self._finish()
        return self.foreground, self.tui, self.workflows

    def _compose_plugins(self) -> None:
        parts = self.parts
        pending = self.pending_notifications

        def display_plugin_notification(interaction: object) -> None:
            if parts.tui_ref:
                parts.tui_ref[0]._append(DisplayKind.METADATA, interaction.prompt)
            else:
                pending.append(interaction)

        self.display_plugin_notification = display_plugin_notification
        self.plugin_events = PluginEventCoordinator(
            parts.plugin_host,
            PluginInteractionAdapter(
                parts.interaction_broker, display_plugin_notification
            ),
            parts.dispatcher,
        )
        self.plugin_commands = PluginCommandController(
            parts.plugin_host,
            discover=parts.plugin_discover,
            on_change=lambda: parts.surface_refresher(
                parts.plugin_host,
                parts.mode_control,
                parts.tui_ref[0],
                parts.on_plugin_change,
            ),
        )

    def _compose_foreground(self) -> None:
        parts = self.parts
        self.workflows = WorkflowService(parts.sessions)
        workflows = self.workflows
        parts.workspace_runtime.set_verification_invalidator(
            lambda task_id, replacement: parts.verification_invalidator(
                parts.sessions, workflows, task_id, replacement
            )
        )
        self.foreground = IntegratedForegroundTaskController(
            parts.controller,
            parts.sessions,
            parts.root,
            profile_supplier=parts.profile_supplier,
            profile_resolver=parts.profile_resolver,
            subagents=parts.subagents,
            workflows=self.workflows,
            plugin_events=self.plugin_events,
            workspace_runtime=parts.workspace_runtime,
        )
        plugin_commands = self.plugin_commands
        self.foreground.subscribe_settled(
            lambda: plugin_commands.apply_staged(idle=True)
        )

    def _compose_tui(self) -> None:
        parts = self.parts
        self.tui = ModeAwareWindowsTerminalApp(
            parts.controller,
            parts.approvals,
            sessions=parts.sessions,
            evidence=parts.sessions,
            history=parts.sessions,
            tasks=self.foreground,
            modes=parts.mode_control,
            permissions=parts.permission_control,
            skills=parts.skills,
            mcp=parts.mcp,
            workflows=parts.sessions,
            command_registry=parts.plugin_registry(parts.plugin_host),
            checkpoints=parts.checkpoints,
            rewind=parts.rewind,
            plugins=self.plugin_commands,
            interaction_broker=parts.interaction_broker,
            diff_source=TaskScopedGitDiffAdapter(
                parts.workspace_runtime,
                lambda: parts.current_diff_task(parts.tui_ref),
                GitDiffAdapter(parts.git),
            ),
            capability=ModePermissionView(
                parts.snapshot,
                PermissionSummary(
                    parts.approval_mode,
                    str(parts.root),
                    parts.approval_mode is ApprovalMode.UNRESTRICTED,
                    parts.approval_mode is not ApprovalMode.PLAN,
                ),
            ),
            recover_pending=parts.workspace_runtime.recover_pending,
            startup=parts.workspace_runtime.startup,
            plugin_errors=parts.plugin_errors,
        )

    def _finish(self) -> None:
        parts = self.parts
        parts.tui_ref.append(self.tui)
        for notification in self.pending_notifications:
            self.display_plugin_notification(notification)
        self.plugin_commands.attach(self.tui)
        tui = self.tui
        parts.subagents.subscribe(
            lambda view: tui.interactions.observe_agent(tui, view)
        )
        parts.workflow_subscriber(parts.subagents, self.workflows)
