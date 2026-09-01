from __future__ import annotations

import os  # Compatibility export for callers patching code_agent_win.app.os.
from dataclasses import replace
from pathlib import Path

from code_agent.attachments.ingest import AttachmentIngestor
from code_agent.attachments.store import AttachmentStore
from code_agent.config.loader import load_runtime_config
from code_agent.context.repo_index import RepoIndexService
from code_agent.context.repo_map import RepoMapViewCache
from code_agent.context.repo_scan import RepoFileScanner
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.orchestration.models import AgentMode
from code_agent.policy.models import ApprovalMode
from code_agent.runtime._powershell_runtime import resolved_powershell_runtime
from code_agent.workspace.windows_paths import require_supported_windows_path
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.skills.controller import SkillController
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.edits import WorkspaceEditor

from code_agent_win.application_context import RuntimeContextFactory
from code_agent_win.application_model import Application
from code_agent_win.context_runtime import build_context_runtime
from code_agent_win.action_dispatcher import RootActionDispatcher
from code_agent_win.host_composition import compose_host
from code_agent_win.multimodal_ui import build_attachment_draft
from code_agent_win.peer_composition import compose_peers
from code_agent_win.agent_modes import build_mode_registry, freeze_mode
from code_agent_win.runtime_support import model_client, profile_model_factory, replace_model
from code_agent_win.runtime_extensions import SkillApprovalAdapter, ThreadRuntimeBinding
from code_agent_win.rewind_runtime import RewindRuntime
from code_agent_win.rewind_sessions import build_rewind_write_side
from code_agent_win.runtime_controls import compose_runtime_controls
from code_agent_win.tool_support import discover_git_workspace
from code_agent_win.ui_composition import compose_ui
from code_agent_win.workspace_runtime import ManagedWorkspaceRuntime
from code_agent_win.workspace_mutation_pool import WorkspaceMutationPool
from code_agent_win.workspace_session_router import WorkspaceSessionRouter
from code_agent_win.workspace_context import workspace_uses_repo_map
from code_agent_win.app_paths import product_state_root as _product_state_root, session_path as _session_path, workspace_storage_path as _workspace_storage_path


_model_client = model_client
__all__ = ("RootActionDispatcher", "create_application")


def create_application(
    workspace_root: Path | None = None,
    *,
    model_name: str | None = None,
    profile_name: str | None = None,
    mode_name: str | None = None,
) -> Application:
    return _ApplicationComposer(
        workspace_root,
        model_name=model_name,
        profile_name=profile_name,
        mode_name=mode_name,
    ).build()


class _ApplicationComposer:
    def __init__(
        self,
        workspace_root: Path | None,
        *,
        model_name: str | None,
        profile_name: str | None,
        mode_name: str | None,
    ) -> None:
        if profile_name is not None and (not isinstance(profile_name, str) or not profile_name.strip()):
            raise ValueError("profile_name must be non-blank text")
        requested_root = workspace_root or Path.cwd()
        require_supported_windows_path(requested_root, operation="workspace root")
        self.root = requested_root.resolve()
        require_supported_windows_path(self.root, operation="workspace root")
        self.product_state_root = _product_state_root()
        self.product_state_root.mkdir(parents=True, exist_ok=True)
        self.workspace_storage_root = _workspace_storage_path()
        self.runtime_config = load_runtime_config(cli_profile=profile_name)
        self.powershell = resolved_powershell_runtime(self.runtime_config.powershell_dialect)
        guard = WorkspacePathGuard(
            self.root,
            allow_sensitive=self.runtime_config.allow_sensitive_paths,
            allow_outside=self.runtime_config.approval_mode in {
                ApprovalMode.UNRESTRICTED,
                ApprovalMode.FULL_LOCAL,
            },
        )
        files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        git = discover_git_workspace(self.root)
        scanner = RepoFileScanner(files)
        self.repo_index = RepoIndexService(files, scan_file=scanner.scan)
        self.repo_view_cache = RepoMapViewCache()
        self.repo_map_enabled = workspace_uses_repo_map(self.root, git_available=git is not None)
        self.profiles = {item.name: item for item in self.runtime_config.profiles}
        self.modes, _ = build_mode_registry(self.profiles, self.runtime_config.profile)
        self.snapshot = freeze_mode(self.modes, self.profiles, mode_name)
        initial_name = self.snapshot.definition.profile_id
        self.initial = self.profiles[initial_name]
        if model_name is not None:
            self.initial = replace_model(self.initial, model_name)
            self.profiles[initial_name] = self.initial
            selected_mode = self.snapshot.definition.mode.value
            self.snapshot = freeze_mode(self.modes, self.profiles, selected_mode)
        self.mode_snapshots = {mode: self.modes.freeze(mode, self.profiles) for mode in AgentMode}

    def build(self) -> Application:
        self._configure_workspace()
        self._configure_rewind()
        self._configure_peers()
        self._configure_context()
        self._configure_host()
        self._configure_controls()
        self._configure_ui()
        return self._finish()

    def _configure_workspace(self) -> None:
        self.session_path = _session_path()
        self.sessions = SQLiteSessionRepository(self.session_path)
        self.workspace_runtime = ManagedWorkspaceRuntime(
            self.sessions,
            self.workspace_storage_root,
            snapshot_read_fallback_roots=(
                self.session_path.parent / "managed-workspaces" / "snapshots",
            ),
            allow_sensitive_paths=self.runtime_config.allow_sensitive_paths,
            powershell=self.powershell,
        )
        self.services = self.workspace_runtime.services_for_root(self.root)
        self.guard = self.services.guard
        self.files = self.services.files
        self.git = self.services.git
        self.repo_index = self.services.repo_index
        self.attachment_store = AttachmentStore(self.product_state_root / "attachments")
        self.attachment_ingestor = AttachmentIngestor(
            self.attachment_store,
            workspace_guard=self.services.guard,
            ignore_rules=self.services.files.ignore,
        )
        self.model_factory = profile_model_factory(_model_client, self.profiles, self.attachment_store)
        self.approvals = ApprovalBroker()

    def _configure_rewind(self) -> None:
        self.rewind_write = build_rewind_write_side(
            self.guard,
            WorkspaceEditor(self.guard),
            self.product_state_root,
            self.session_path,
            has_git=self.git is not None,
        )
        self.mutations = WorkspaceMutationPool(self.services, self.rewind_write.capture)
        self.sessions = WorkspaceSessionRouter(
            self.rewind_write.base,
            self.root,
            self.workspace_runtime.root_for_thread,
            lambda root: self.mutations.coordinated_for_services(
                self.workspace_runtime.services_for_root(root)
            ),
        )
        self.workspace_runtime._sessions = self.sessions
        self.workspace_runtime.configure_mutations(self.mutations, self.root)
        self.rewind = RewindRuntime(
            self.rewind_write.base,
            self.rewind_write.snapshots,
            self.rewind_write.capture.editor,
        )
        self.skills = SkillController(self.root, self.sessions, SkillApprovalAdapter(self.approvals))
        self.thread_binding = ThreadRuntimeBinding()

    def _configure_peers(self) -> None:
        (
            self.tui_ref, self.activity_lock, self.peer_service,
            self.peer_tools, self.peers,
        ) = compose_peers(
            self.sessions, self.root, self.runtime_config.approval_mode.value
        )

    def _configure_context(self) -> None:
        self.context_for = RuntimeContextFactory(
            self.root,
            git_available=self.git is not None,
            repo_map_enabled=self.repo_map_enabled,
            guard=self.guard,
            files=self.files,
            repo_index=self.repo_index,
            repo_view_cache=self.repo_view_cache,
            sessions=self.sessions,
            thread_binding=self.thread_binding,
            skills=self.skills,
            workspace_runtime=self.workspace_runtime,
            powershell=self.powershell,
            context_runtime_factory=build_context_runtime,
        )

    def _configure_host(self) -> None:
        (
            self.dispatcher,
            self.mcp,
            self.plugin_host,
            self.plugin_errors,
            self.plugin_bridge,
            self.interaction_broker,
            self.plugin_discover,
            self.plugin_bindings,
        ) = compose_host(
            root=self.root,
            services=self.services,
            workspace_runtime=self.workspace_runtime,
            runtime_config=self.runtime_config,
            modes=self.modes,
            sessions=self.sessions,
            approvals=self.approvals,
            thread_binding=self.thread_binding,
            peers=self.peer_tools,
            capture=self.rewind_write.capture,
            mutations=self.mutations,
        )

    def _configure_controls(self) -> None:
        self.application_ref: list[Application] = []
        self.controls = compose_runtime_controls(
            root=self.root, snapshot=self.snapshot,
            mode_snapshots=self.mode_snapshots, profiles=self.profiles,
            modes=self.modes, initial=self.initial,
            client_factory=self.model_factory,
            context_for=self.context_for, dispatcher=self.dispatcher,
            sessions=self.sessions, thread_binding=self.thread_binding,
            plugin_host=self.plugin_host, plugin_bridge=self.plugin_bridge,
            plugin_bindings=self.plugin_bindings,
            approval_mode=self.runtime_config.approval_mode,
            application_ref=self.application_ref, tui_ref=self.tui_ref,
            context_wrapper=self.peers.wrap_context,
            activity_lock=self.activity_lock,
        )
        self.snapshot = self.controls.runtime_selection.snapshot

    def _configure_ui(self) -> None:
        self.foreground, self.tui, self.workflows = compose_ui(
            controller=self.controls.controller,
            approvals=self.approvals,
            sessions=self.sessions,
            root=self.root,
            profile_supplier=self.controls.profile_facts,
            profile_resolver=self.controls.profile_resolver,
            runtime_resolver=self.controls.runtime_resolver,
            runtime_selection=self.controls.runtime_selection,
            peers=self.peers,
            subagents=self.controls.subagents,
            snapshot=self.snapshot,
            approval_mode=self.runtime_config.approval_mode,
            mode_control=self.controls.mode_control,
            permission_control=self.controls.permission_control,
            plugin_host=self.plugin_host,
            dispatcher=self.dispatcher,
            interaction_broker=self.interaction_broker,
            skills=self.skills,
            mcp=self.mcp,
            git=self.git,
            checkpoints=self.workspace_runtime.checkpoint_control(),
            rewind=self.rewind,
            workspace_runtime=self.workspace_runtime,
            plugin_errors=self.plugin_errors,
            plugin_discover=self.plugin_discover,
            on_plugin_change=self.plugin_bindings.refresh,
            tui_ref=self.tui_ref,
            attachment_draft=build_attachment_draft(
                self.attachment_ingestor,
                lambda: self.controls.manager.current.profile,
            ),
        )

    def _finish(self) -> Application:
        application = Application(
            controller=self.controls.controller,
            foreground_tasks=self.foreground,
            tui=self.tui,
            dispatcher=self.dispatcher,
            model=self.controls.manager,
            mcp=self.mcp,
            mode=self.snapshot,
            plugins=self.plugin_host,
            subagents=self.controls.subagents,
            rewind=self.rewind,
            repo_index=self.repo_index,
            workflows=self.workflows,
            workspace_runtime=self.workspace_runtime,
            attachment_store=self.attachment_store,
            attachment_ingestor=self.attachment_ingestor,
            runtime_selection=self.controls.runtime_selection,
            peers=self.peers,
        )
        self.application_ref.append(application)
        return application
