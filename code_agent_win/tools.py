from __future__ import annotations

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


TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        "read_file",
        "Read a UTF-8 workspace file.",
        _object_schema({"path": _nonempty_text_schema()}, ("path",)),
    ),
    ToolDefinition(
        "list_files",
        "List visible workspace files.",
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
        "run_command",
        "Run an approved PowerShell command.",
        _object_schema({"command": _nonempty_text_schema()}, ("command",)),
    ),
)

_TOOLS_BY_NAME = {tool.name: tool for tool in TOOL_DEFINITIONS}


def tool_definitions() -> tuple[ToolDefinition, ...]:
    """Return the provider-facing definitions used by the action dispatcher."""
    return TOOL_DEFINITIONS


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
        return (
            isinstance(value, str)
            and isinstance(minimum, int)
            and not isinstance(minimum, bool)
            and len(value) >= minimum
        )
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
