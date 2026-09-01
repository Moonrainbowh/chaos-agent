from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import PureWindowsPath

from code_agent.core.models import ToolDefinition
from code_agent.runtime.models import PowerShellRuntimeInfo
from code_agent.runtime.output_codec import OutputEncoding
from code_agent_win.edit_plan_tools import (
    EDIT_PLAN_TOOL_DEFINITIONS,
    validate_edit_plan_tool_arguments,
)
from code_agent_win.tool_schema import (
    integer_schema as _integer_schema,
    matches_schema as _matches_schema,
    nonempty_text_schema as _nonempty_text_schema,
    object_schema as _object_schema,
)


def _output_encoding_schema() -> dict[str, object]:
    return {"type": "string", "enum": [item.value for item in OutputEncoding]}


def _text_encoding_schema() -> dict[str, object]:
    return {"type": "string", "enum": ["auto", "windows-ansi", "windows-oem"]}


TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        "read_file",
        "Read a strictly decoded workspace text file and report its format.",
        _object_schema(
            {"path": _nonempty_text_schema(), "encoding": _text_encoding_schema()},
            ("path",),
        ),
    ),
    ToolDefinition(
        "list_files",
        "List up to 200 visible files. Omit root for the workspace root; do not "
        "repeat the same listing when its prior result is already available.",
        _object_schema({"root": _nonempty_text_schema()}),
    ),
    ToolDefinition(
        "search_text",
        "Search visible workspace text.",
        _object_schema(
            {
                "pattern": _nonempty_text_schema(),
                "regex": {"type": "boolean"},
                "case_sensitive": {"type": "boolean"},
            },
            ("pattern",),
        ),
    ),
    ToolDefinition(
        "write_file",
        "Atomically write text while preserving an existing file's format.",
        _object_schema(
            {
                "path": _nonempty_text_schema(),
                "content": _nonempty_text_schema(),
                "encoding": _text_encoding_schema(),
            },
            ("path", "content"),
        ),
    ),
    ToolDefinition(
        "replace_text",
        "Replace one exact text occurrence.",
        _object_schema(
            {
                "path": _nonempty_text_schema(),
                "old_text": _nonempty_text_schema(),
                "new_text": _nonempty_text_schema(),
                "encoding": _text_encoding_schema(),
            },
            ("path", "old_text", "new_text"),
        ),
    ),
    *EDIT_PLAN_TOOL_DEFINITIONS,
    ToolDefinition("git_status", "Read Git porcelain status.", _object_schema({})),
    ToolDefinition(
        "git_diff",
        "Read Git diff for workspace paths.",
        _object_schema(
            {"paths": {"type": "array", "items": _nonempty_text_schema()}}
        ),
    ),
    ToolDefinition(
        "run_verification",
        "Run a registered local verification with fixed arguments.",
        _object_schema(
            {
                "kind": {"type": "string", "enum": ["python_unittest", "pytest", "python_compileall", "python_build", "node_test", "node_build", "node_lint", "dotnet_test", "dotnet_build"]},
                "cwd": _nonempty_text_schema(),
                "targets": {"type": "array", "items": _nonempty_text_schema()},
                "timeout_s": _integer_schema(1, 900),
            },
            ("kind",),
        ),
    ),
    ToolDefinition(
        "run_command",
        "Run an approved script in the frozen PowerShell dialect. Use the active "
        "PowerShell syntax only; errors stop by default, while explicit catch, "
        "Continue, SilentlyContinue, or Ignore remain under script control; "
        "pipe multiple stdin lines with @('line1', 'line2') | command and never "
        "use the Bash here-string operator <<<.",
        _object_schema({"command": _nonempty_text_schema()}, ("command",)),
    ),
    ToolDefinition(
        "run_process_v1",
        "Start one approved program with an explicit argv. No shell parsing, "
        "expansion, redirection, pipelines, environment overrides, or stdin. "
        "stdout/stderr default to strict UTF-8; declare a Windows or UTF-16 "
        "encoding when the program uses one.",
        _object_schema(
            {
                "program": {"type": "string", "minLength": 1, "maxLength": 4096},
                "args": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 8192},
                    "maxItems": 128,
                },
                "cwd": {"type": "string", "minLength": 1, "maxLength": 4096},
                "timeout_s": _integer_schema(1, 900),
                "stdout_encoding": _output_encoding_schema(),
                "stderr_encoding": _output_encoding_schema(),
            },
            ("program", "args"),
        ),
    ),
    ToolDefinition(
        "delegate_agent",
        "Delegate one bounded objective to an advisory subagent, Oracle, reviewer, searcher, or librarian.",
        _object_schema(
            {
                "objective": _nonempty_text_schema(),
                "role": {
                    "type": "string",
                    "enum": ["subagent", "oracle", "review", "search", "librarian"],
                },
                "agent_id": _nonempty_text_schema(),
                "token_budget": _integer_schema(256, 100000),
                "tool_budget": _integer_schema(0, 128),
                "active_seconds": _integer_schema(1, 1800),
            },
            ("objective",),
        ),
    ),
)

_TOOLS_BY_NAME = {tool.name: tool for tool in TOOL_DEFINITIONS}
_GIT_TOOLS = {"git_status", "git_diff"}
_BASH_HERE_STRING = re.compile(r"(?<![\w'\"`])<<<(?=\s|['\"])")
_SHELL_LAUNCHERS = frozenset(
    {"pwsh", "pwsh.exe", "powershell", "powershell.exe", "cmd", "cmd.exe",
     "bash", "bash.exe", "sh", "sh.exe", "wsl", "wsl.exe"}
)


def tool_definitions(
    *,
    include_git: bool = True,
    powershell: PowerShellRuntimeInfo | None = None,
) -> tuple[ToolDefinition, ...]:
    """Return the provider-facing definitions used by the action dispatcher."""
    definitions = TOOL_DEFINITIONS
    if powershell is not None:
        definitions = tuple(
            _powershell_tool(tool, powershell) for tool in definitions
        )
    if include_git:
        return definitions
    return tuple(tool for tool in definitions if tool.name not in _GIT_TOOLS)


def _powershell_tool(
    tool: ToolDefinition, powershell: PowerShellRuntimeInfo
) -> ToolDefinition:
    if tool.name != "run_command":
        return tool
    return ToolDefinition(
        tool.name,
        f"Run an approved {powershell.prompt_summary} script. Use only this "
        "dialect; errors stop by default, explicit recovery remains under "
        "script control; pipe stdin explicitly and never use Bash <<< syntax.",
        tool.parameters,
    )


def powershell_compatibility_error(command: str) -> str | None:
    """Reject an unambiguous Bash here-string before policy or execution."""
    if _BASH_HERE_STRING.search(command):
        return (
            "Bash here-string operator <<< is not supported by PowerShell. "
            "Pipe input with @('line1', 'line2') | command."
        )
    return None


def process_compatibility_error(arguments: Mapping[str, object]) -> str | None:
    """Reject shell launchers and invalid Windows argv before authorization."""
    program = arguments.get("program")
    raw_args = arguments.get("args")
    cwd = arguments.get("cwd", ".")
    if not isinstance(program, str) or not program.strip():
        return "program must be non-blank text"
    if "\x00" in program or not isinstance(cwd, str) or "\x00" in cwd:
        return "process fields must not contain NUL characters"
    if not isinstance(raw_args, Sequence) or isinstance(
        raw_args, (str, bytes, bytearray)
    ):
        return "args must be an array of strings"
    if any(not isinstance(item, str) or "\x00" in item for item in raw_args):
        return "process arguments must be strings without NUL characters"
    executable_name = PureWindowsPath(program).name.casefold()
    if executable_name in _SHELL_LAUNCHERS or executable_name.endswith(
        (".cmd", ".bat")
    ):
        return "shell launchers are not allowed; use run_command for PowerShell"
    if len(subprocess.list2cmdline((program, *raw_args))) > 30_000:
        return "rendered Windows command line exceeds 30000 characters"
    return None


def validate_tool_arguments(name: str, arguments: Mapping[str, object]) -> str | None:
    """Return a safe validation error before policy or tool execution begins."""
    definition = _TOOLS_BY_NAME.get(name)
    if definition is None:
        return None
    parameters = definition.parameters
    properties = parameters["properties"]
    required = parameters["required"]
    if not isinstance(properties, Mapping) or not isinstance(required, Sequence):
        raise RuntimeError("invalid built-in tool schema")
    unexpected = set(arguments).difference(properties)
    if unexpected:
        return "unexpected argument"
    for field in required:
        if field not in arguments:
            return f"missing required argument: {field}"
    for field, value in arguments.items():
        schema = properties[field]
        if not isinstance(schema, Mapping) or not _matches_schema(value, schema):
            return f"invalid argument type: {field}"
    return validate_edit_plan_tool_arguments(name, arguments)
