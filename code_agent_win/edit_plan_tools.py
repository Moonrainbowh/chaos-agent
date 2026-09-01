from __future__ import annotations

from collections.abc import Mapping, Sequence

from code_agent.core.models import ToolDefinition

from code_agent_win.tool_schema import (
    nonempty_text_schema,
    object_schema,
    text_schema,
)


MAX_EDIT_PLAN_OPERATIONS = 32
EDIT_OPERATION_KINDS = ("write", "replace", "delete", "move")
_ENCODING_SCHEMA = {
    "type": "string",
    "enum": ["auto", "windows-ansi", "windows-oem"],
}
_OPERATION_SCHEMA = object_schema(
    {
        "kind": {"type": "string", "enum": list(EDIT_OPERATION_KINDS)},
        "path": nonempty_text_schema(),
        "content": text_schema(),
        "old_text": nonempty_text_schema(),
        "new_text": text_schema(),
        "source_path": nonempty_text_schema(),
        "destination_path": nonempty_text_schema(),
        "encoding": _ENCODING_SCHEMA,
    },
    ("kind",),
)


EDIT_PLAN_TOOL_DEFINITIONS = (
    ToolDefinition(
        "plan_workspace_edits_v1",
        "Build one immutable, exact multi-file workspace edit plan without applying it.",
        object_schema(
            {
                "operations": {
                    "type": "array",
                    "items": _OPERATION_SCHEMA,
                    "minItems": 1,
                    "maxItems": MAX_EDIT_PLAN_OPERATIONS,
                },
                "supersedes_plan_id": nonempty_text_schema(),
            },
            ("operations",),
        ),
    ),
    ToolDefinition(
        "apply_workspace_edit_plan_v1",
        "Apply one locally stored immutable edit plan by exact identity.",
        object_schema(
            {
                "plan_id": nonempty_text_schema(),
                "plan_digest": {
                    "type": "string",
                    "minLength": 64,
                    "maxLength": 64,
                    "pattern": "^[0-9a-f]{64}$",
                },
            },
            ("plan_id", "plan_digest"),
        ),
    ),
)


_FIELDS = {
    "write": (frozenset({"kind", "path", "content"}), frozenset({"encoding"})),
    "replace": (
        frozenset({"kind", "path", "old_text", "new_text"}),
        frozenset({"encoding"}),
    ),
    "delete": (frozenset({"kind", "path"}), frozenset()),
    "move": (
        frozenset({"kind", "source_path", "destination_path"}),
        frozenset(),
    ),
}


def validate_edit_plan_tool_arguments(
    name: str, arguments: Mapping[str, object]
) -> str | None:
    if name != "plan_workspace_edits_v1":
        return None
    operations = arguments.get("operations")
    if not isinstance(operations, Sequence) or isinstance(operations, (str, bytes)):
        return "operations must be an array"
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping):
            return f"operation {index} must be an object"
        kind = operation.get("kind")
        fields = _FIELDS.get(kind) if isinstance(kind, str) else None
        if fields is None:
            return f"operation {index} has an invalid kind"
        required, optional = fields
        present = frozenset(operation)
        if not required.issubset(present):
            return f"operation {index} is missing a required field"
        if not present.issubset(required | optional):
            return f"operation {index} has a field invalid for {kind}"
        if kind == "move" and operation["source_path"] == operation["destination_path"]:
            return f"operation {index} move paths must differ"
    return None


__all__ = [
    "EDIT_OPERATION_KINDS",
    "EDIT_PLAN_TOOL_DEFINITIONS",
    "MAX_EDIT_PLAN_OPERATIONS",
    "validate_edit_plan_tool_arguments",
]
