from __future__ import annotations

import os
from pathlib import Path

from code_agent.core.models import ActionRequest, ActionResult
from code_agent.runtime.models import (
    CommandResult,
    PowerShellRuntimeInfo,
    StreamName,
    TerminationReason,
)
from code_agent.runtime.output_codec import (
    DecodedOutput,
    OutputDecodeStatus,
    OutputEncoding,
    decode_output,
)
from code_agent.verification.registry import failure_fingerprint
from code_agent.workspace.errors import WorkspaceError
from code_agent.workspace.git import GitCommandError, GitWorkspace
from code_agent.workspace.windows_paths import windows_path_support


_BASE_SYSTEM_PROMPT = """You are a careful coding agent running on Windows.
Use only the frozen PowerShell dialect reported below for run_command, and never
use Bash-only redirection such as <<< or <<HEREDOC. To provide multiple stdin
lines, use @('line1', 'line2') | command. run_command uses Stop as the default
PowerShell error action; catch, Continue, SilentlyContinue, or Ignore only when
recovery is intentional. run_process_v1 starts program + args directly: it
performs no shell parsing, expansion, redirection, pipelines, variable interpolation, environment
override, or stdin. Treat tool errors and nonzero exits as failures that must be
diagnosed before claiming completion."""


def windows_system_prompt(
    git_available: bool,
    powershell: PowerShellRuntimeInfo | None = None,
) -> str:
    """Describe the fixed command dialect and current Git capability."""
    git_note = (
        "The current workspace is a Git repository; use the typed Git tools."
        if git_available
        else "The current workspace is not a Git repository; Git tools are unavailable."
    )
    shell_note = (
        f"Frozen PowerShell runtime: {powershell.prompt_summary}."
        if powershell is not None
        else "PowerShell runtime information is unavailable until host resolution."
    )
    path_note = f"Windows path support: {windows_path_support().summary}."
    return f"{_BASE_SYSTEM_PROMPT}\n{shell_note}\n{path_note}\n{git_note}"


def discover_git_workspace(root: os.PathLike[str] | str) -> GitWorkspace | None:
    """Return the fixed Git adapter only when the workspace is a repository."""
    try:
        workspace = GitWorkspace(Path(root))
        return workspace if workspace.is_repository() else None
    except (WorkspaceError, OSError):
        return None


def command_action_result(
    request: ActionRequest,
    result: CommandResult,
    *,
    powershell: PowerShellRuntimeInfo | None = None,
    stdout_encoding: OutputEncoding = OutputEncoding.UTF_8,
    stderr_encoding: OutputEncoding = OutputEncoding.UTF_8,
) -> ActionResult:
    """Convert runtime completion into truthful tool success or failure."""
    stdout = decode_output(
        result.stdout,
        stdout_encoding,
        truncated=StreamName.STDOUT in result.truncated_streams,
    )
    stderr = decode_output(
        result.stderr,
        stderr_encoding,
        truncated=StreamName.STDERR in result.truncated_streams,
    )
    truncated_streams = sorted(item.value for item in result.truncated_streams)
    output: dict[str, object] = {
        "returncode": result.returncode,
        "reason": result.reason.value,
        "cwd": result.cwd,
        "truncated": result.truncated,
        "truncated_streams": truncated_streams,
        **_decoded_fields("stdout", stdout),
        **_decoded_fields("stderr", stderr),
    }
    if powershell is not None:
        output["powershell"] = powershell.to_public_dict()
    command_failed = (
        result.reason is not TerminationReason.EXITED or result.returncode != 0
    )
    decoding_failed = any(
        item.status is not OutputDecodeStatus.DECODED for item in (stdout, stderr)
    )
    failed = command_failed or decoding_failed
    if failed:
        output["error"] = (
            "command failed" if command_failed else "command output decoding failed"
        )
    metadata: dict[str, object] = {
        "execution_attempted": True,
        "returncode": result.returncode,
        "cwd": result.cwd,
        "truncated": result.truncated,
        "truncated_streams": truncated_streams,
        "decoding_failed": decoding_failed,
    }
    if failed:
        metadata["failure_fingerprint"] = failure_fingerprint(
            result.stdout, result.stderr, result.returncode
        )
    return ActionResult(
        request.id,
        request.name,
        output,
        is_error=failed,
        metadata=metadata,
    )


def _decoded_fields(name: str, decoded: DecodedOutput) -> dict[str, object]:
    fields: dict[str, object] = {
        name: decoded.text,
        f"{name}_encoding": decoded.encoding.value,
        f"{name}_decoding": decoded.status.value,
    }
    if decoded.code_page is not None:
        fields[f"{name}_code_page"] = decoded.code_page
    if decoded.base64_data is not None:
        fields[f"{name}_base64"] = decoded.base64_data
    return fields


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
