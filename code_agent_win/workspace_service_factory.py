from __future__ import annotations

from pathlib import Path

from code_agent.context.repo_index import RepoIndexService
from code_agent.context.repo_scan import RepoFileScanner
from code_agent.runtime._powershell_runtime import PowerShellRuntimeResolver
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.verification.local_adapter import LocalVerificationAdapter
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard

from code_agent_win.tool_support import discover_git_workspace
from code_agent_win.workspace_models import WorkspaceServices


def build_workspace_services(
    root: Path,
    *,
    allow_sensitive_paths: bool,
    powershell: PowerShellRuntimeResolver,
) -> WorkspaceServices:
    guard = WorkspacePathGuard(root, allow_sensitive=allow_sensitive_paths)
    files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
    git = discover_git_workspace(root)
    repo_index = RepoIndexService(
        files, scan_file=RepoFileScanner(files).scan
    )
    return WorkspaceServices(
        root,
        guard,
        files,
        git,
        repo_index,
        WindowsLocalRuntime(root, powershell=powershell),
        LocalVerificationAdapter(root),
    )
