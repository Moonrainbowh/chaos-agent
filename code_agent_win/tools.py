from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from code_agent.core.models import ToolDefinition


def _object_schema(
    properties: Mapping[str, object], required: Sequence[str] = ()
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _nonempty_text_schema() -> dict[str, object]:
    return {"type": "string", "minLength": 1}


def _integer_schema(minimum: int, maximum: int) -> dict[str, object]:
    return {"type": "integer", "minimum": minimum, "maximum": maximum}


TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        "read_file",
        "Read a UTF-8 workspace file.",
        _object_schema({"path": _nonempty_text_schema()}, ("path",)),
    ),
    ToolDefinition(
        "list_files", "List visible workspace files.",
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
        "Atomically write a reviewed workspace file.",
        _object_schema(
            {"path": _nonempty_text_schema(), "content": _nonempty_text_schema()},
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
            },
            ("path", "old_text", "new_text"),
        ),
    ),
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
        "Run an approved Windows PowerShell command. Use PowerShell syntax only; "
        "pipe multiple stdin lines with @('line1', 'line2') | command and never "
        "use the Bash here-string operator <<<.",
        _object_schema({"command": _nonempty_text_schema()}, ("command",)),
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
                "token_budget": _integer_schema(256, 100000),
                "tool_budget": _integer_schema(0, 128),
                "active_seconds": _integer_schema(1, 1800),
            },
            ("objective", "role"),
        ),
    ),
)

_TOOLS_BY_NAME = {tool.name: tool for tool in TOOL_DEFINITIONS}
_GIT_TOOLS = {"git_status", "git_diff"}
_BASH_HERE_STRING = re.compile(r"(?<![\w'\"`])<<<(?=\s|['\"])")


def tool_definitions(*, include_git: bool = True) -> tuple[ToolDefinition, ...]:
    """Return the provider-facing definitions used by the action dispatcher."""
    if include_git:
        return TOOL_DEFINITIONS
    return tuple(tool for tool in TOOL_DEFINITIONS if tool.name not in _GIT_TOOLS)


def powershell_compatibility_error(command: str) -> str | None:
    """Reject an unambiguous Bash here-string before policy or execution."""
    if _BASH_HERE_STRING.search(command):
        return (
            "Bash here-string operator <<< is not supported by PowerShell. "
            "Pipe input with @('line1', 'line2') | command."
        )
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
    return None


def _matches_schema(value: object, schema: Mapping[str, object]) -> bool:
    schema_type = schema.get("type")
    if schema_type == "string":
        minimum = schema.get("minLength", 0)
        matches = (
            isinstance(value, str)
            and isinstance(minimum, int)
            and not isinstance(minimum, bool)
            and len(value) >= minimum
        )
        options = schema.get("enum")
        return matches and (not isinstance(options, Sequence) or value in options)
    if schema_type == "integer":
        minimum, maximum = schema.get("minimum"), schema.get("maximum")
        return isinstance(value, int) and not isinstance(value, bool) and isinstance(minimum, int) and isinstance(maximum, int) and minimum <= value <= maximum
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "array":
        items = schema.get("items")
        return (
            isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray))
            and isinstance(items, Mapping)
            and all(_matches_schema(item, items) for item in value)
        )
    return False
