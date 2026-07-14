from __future__ import annotations

import os
from pathlib import Path

from code_agent.core.models import ActionRequest, ActionResult
from code_agent.runtime.models import CommandResult, TerminationReason
from code_agent.workspace.errors import WorkspaceError
from code_agent.workspace.git import GitCommandError, GitWorkspace


_BASE_SYSTEM_PROMPT = """You are a careful coding agent running on Windows.
The run_command tool executes Windows PowerShell scripts only. Use PowerShell
syntax and never use Bash-only redirection such as <<< or <<HEREDOC. To provide
multiple standard-input lines, use @('line1', 'line2') | command. Treat tool
errors and nonzero command exits as failures that must be diagnosed before
claiming completion."""


def windows_system_prompt(git_available: bool) -> str:
    """Describe the fixed command dialect and current Git capability."""
    git_note = (
        "The current workspace is a Git repository; use the typed Git tools."
        if git_available
        else "The current workspace is not a Git repository; Git tools are unavailable."
    )
    return f"{_BASE_SYSTEM_PROMPT}\n{git_note}"


def discover_git_workspace(root: os.PathLike[str] | str) -> GitWorkspace | None:
    """Return the fixed Git adapter only when the workspace is a repository."""
    try:
        workspace = GitWorkspace(Path(root))
        return workspace if workspace.is_repository() else None
    except (WorkspaceError, OSError):
        return None


def command_action_result(
    request: ActionRequest, result: CommandResult
) -> ActionResult:
    """Convert runtime completion into truthful tool success or failure."""
    output: dict[str, object] = {
        "returncode": result.returncode,
        "stdout": result.stdout.decode("utf-8", "replace"),
        "stderr": result.stderr.decode("utf-8", "replace"),
        "reason": result.reason.value,
    }
    failed = (
        result.reason is not TerminationReason.EXITED or result.returncode != 0
    )
    if failed:
        output["error"] = "command failed"
    return ActionResult(
        request.id,
        request.name,
        output,
        is_error=failed,
    )


def git_error_result(
    request: ActionRequest, error: GitCommandError
) -> ActionResult:
    """Expose a bounded Git diagnostic instead of only the exception type."""
    detail = _safe_detail(error.stderr or str(error))
    return ActionResult(
        request.id,
        request.name,
        {"error": "git command failed", "detail": detail},
        is_error=True,
    )


def _safe_detail(value: str, max_characters: int = 2_000) -> str:
    cleaned = "".join(
        character
        if character >= " " or character in {"\r", "\n", "\t"}
        else "?"
        for character in value
    ).strip()
    return cleaned[:max_characters] or "Git command failed without error output."
