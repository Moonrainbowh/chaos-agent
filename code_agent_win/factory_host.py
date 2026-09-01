from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_agent.attachments.ingest import AttachmentIngestor
from code_agent.attachments.store import AttachmentStore
from code_agent.interfaces.approval import ApprovalBroker
from code_agent.mcp.official_sdk import OfficialMcpSdkAdapter
from code_agent.mcp.registry import McpController, McpRegistry
from code_agent.mcp.stdio_manager import StdioMcpManager
from code_agent.policy.engine import ActionPolicy, PolicyConfig
from code_agent.providers.config import ModelProfile
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.runtime._powershell_runtime import (
    PowerShellRuntimeResolver,
    resolved_powershell_runtime,
)
from code_agent.skills.registry import SkillActivation, SkillRegistry
from code_agent.verification.local_adapter import LocalVerificationAdapter
from code_agent.workspace.edits import WorkspaceEditor

from code_agent_win.action_dispatcher import RootActionDispatcher
from code_agent_win.app_models import FactoryHost
from code_agent_win.plugin_runtime import PluginToolBridge, load_plugins
from code_agent_win.rewind_sessions import build_rewind_write_side
from code_agent_win.runtime_support import host_risks
from code_agent_win.tools import tool_definitions


@dataclass(frozen=True)
class _Extensions:
    skills: object
    approvals: ApprovalBroker
    mcp: McpController
    plugin_host: object
    errors: tuple[str, ...]
    bridge: PluginToolBridge
    policy: ActionPolicy


def build_host_integrations(
    root: Path, guard: object, files: object, git: object, cache: object,
    repo_index: object, repo_view_cache: object, repo_map_enabled: bool,
    config: Any, profiles: dict[str, ModelProfile], modes: Any, mode: object,
    initial: ModelProfile, session_path_factory: Callable[[], Path],
    product_state_root: Path,
) -> FactoryHost:
    extensions = _extensions(root, modes, git, config)
    editor = WorkspaceEditor(guard)
    rewind_write = build_rewind_write_side(
        guard,
        editor,
        product_state_root,
        session_path_factory(),
        has_git=git is not None,
    )
    dispatcher = _dispatcher(
        root, files, git, repo_index, editor, rewind_write, extensions,
        resolved_powershell_runtime(config.powershell_dialect),
    )
    store = AttachmentStore(product_state_root / "attachments")
    ingestor = AttachmentIngestor(
        store,
        workspace_guard=guard,
        ignore_rules=files.ignore,
    )
    return FactoryHost(
        root, files, guard, git, cache, repo_index, repo_view_cache,
        repo_map_enabled, config, profiles, modes, mode, initial,
        extensions.skills, extensions.approvals, extensions.mcp,
        extensions.plugin_host, extensions.errors, extensions.bridge,
        dispatcher, rewind_write.coordinated, rewind_write, store, ingestor,
    )


def _extensions(root: Path, modes: object, git: object, config: Any) -> _Extensions:
    skills = SkillActivation(SkillRegistry.discover(root))
    approvals = ApprovalBroker()
    risks: dict[str, str] = {"delegate_agent": "write"}
    manager = StdioMcpManager(
        lambda server: OfficialMcpSdkAdapter(tool_risks=server.tool_risks)
    )
    mcp = McpController(McpRegistry(config.mcp_servers), manager, risks)
    plugin_host, errors = load_plugins(
        root,
        modes,
        host_actions=tuple(
            tool.name for tool in tool_definitions(include_git=git is not None)
        ),
        host_risks=host_risks(),
        controllers=("review", "task", "session"),
    )
    bridge = PluginToolBridge(plugin_host)
    risks.update(bridge.risk_map())
    policy = ActionPolicy(
        PolicyConfig(config.approval_mode, workspace_root=root, mcp_risks=risks)
    )
    return _Extensions(
        skills, approvals, mcp, plugin_host, errors, bridge, policy
    )


def _dispatcher(
    root: Path,
    files: object,
    git: object,
    repo_index: object,
    editor: WorkspaceEditor,
    rewind_write: object,
    extensions: _Extensions,
    powershell: PowerShellRuntimeResolver,
) -> RootActionDispatcher:
    def invalidate_workspace_context(paths: tuple[str, ...]) -> None:
        repo_index.invalidate(paths)
        files.invalidate_inventory()

    return RootActionDispatcher(
        files,
        editor,
        extensions.policy,
        extensions.approvals,
        git=git,
        runtime=WindowsLocalRuntime(root, powershell=powershell),
        verification=LocalVerificationAdapter(root),
        mcp=extensions.mcp,
        plugins=extensions.bridge,
        capture=rewind_write.capture,
        invalidate_cache=invalidate_workspace_context,
    )


__all__ = ["build_host_integrations"]
