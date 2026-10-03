"""Small model interfaces over already authorized typed operations."""
from collections.abc import Sequence
from code_agent.core.models import ActionRequest, ToolDefinition
from code_agent.core._json import plain

OPERATIONS = {
    "read": {"file": "read_file", "slices": "read_code_slices"},
    "search": {"files": "list_files", "text": "search_text"},
    "write": {"file": "write_file"},
    "edit": {"replace": "replace_text", "plan": "plan_workspace_edits_v1", "apply": "apply_workspace_edit_plan_v1"},
    "execute": {"command": "run_command", "process": "run_process_v1", "status": "git_status", "diff": "git_diff", "verify": "run_verification"},
    "web": {"search": "web_search", "fetch": "web_fetch", "site": "site_api", "browser": "browser_fetch", "retrieve": "web_retrieve"},
}
COMMON_TOOL_NAMES = frozenset({"read", "search", "write", "edit", "execute"})


def compact_definitions(tools: Sequence[ToolDefinition]) -> tuple[ToolDefinition, ...]:
    """Merge only operations present in an already restricted tool snapshot."""
    by_name = {tool.name: tool for tool in tools}
    folded = {target for group in OPERATIONS.values() for target in group.values()}
    result = []
    for name, operations in OPERATIONS.items():
        available = {op: by_name[target] for op, target in operations.items() if target in by_name}
        if not available:
            continue
        properties = {}
        descriptions = []
        for op, tool in available.items():
            schema = plain(tool.parameters)
            for field, spec in schema["properties"].items():
                if field in properties and properties[field] != spec:
                    properties[field] = {"anyOf": [properties[field], spec]}
                else:
                    properties[field] = spec
            required = ", ".join(schema.get("required", ())) or "none"
            descriptions.append(f"{op} (required: {required}): {tool.description}")
        properties["operation"] = {"type": "string", "enum": list(available)}
        result.append(ToolDefinition(name,
            "Choose operation; supply only its fields. " + "\n".join(descriptions),
            {"type": "object", "properties": properties, "required": ["operation"], "additionalProperties": False}))
    return tuple(result) + tuple(tool for tool in tools if tool.name not in folded)


def expand_request(request: ActionRequest, tools: Sequence[ToolDefinition]) -> ActionRequest:
    """Translate a compact request without changing its action ID or authorization."""
    operations = OPERATIONS.get(request.name)
    if operations is None:
        return request
    operation = request.arguments.get("operation")
    if not isinstance(operation, str) or operation not in operations:
        raise ValueError("unknown or missing operation")
    target = operations[operation]
    definition = next((tool for tool in tools if tool.name == target), None)
    if definition is None:
        raise ValueError("operation is outside the current mode and role")
    arguments = {key: plain(value) for key, value in request.arguments.items() if key != "operation"}
    unexpected = set(arguments) - set(definition.parameters["properties"])
    missing = set(definition.parameters.get("required", ())) - set(arguments)
    if unexpected or missing:
        raise ValueError("unexpected fields: " + ", ".join(sorted(unexpected)) + "; missing fields: " + ", ".join(sorted(missing)))
    return ActionRequest(request.id, target, arguments)
