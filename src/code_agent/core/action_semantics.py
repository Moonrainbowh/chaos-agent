"""Host-resolved operation identities for observation, never authorization."""
from .models import ActionRequest, ToolCall

READ_ONLY_TOOLS = frozenset({
    "read_file", "read_code_slices", "search_text", "list_files",
    "git_status", "git_diff",
})
PROGRESS_TOOLS = frozenset({
    "write_file", "replace_text", "apply_workspace_edit_plan_v1",
    "run_verification", "new_context",
})


def resolve_supervision_call(call: ToolCall, dispatcher: object) -> ToolCall:
    """Use the current Host resolver while retaining the original call ID.

    Invalid or unavailable operations stay opaque; no model-provided metadata
    can supply a category or grant permission. Execution still uses the call.
    """
    resolver = getattr(dispatcher, "resolve_supervision_action", None)
    if not callable(resolver):
        resolver = getattr(dispatcher, "resolve_action", None)
    if not callable(resolver):
        return call
    try:
        request = resolver(ActionRequest(call.id, call.name, call.arguments))
    except ValueError:
        return call
    if not isinstance(request, ActionRequest) or request.id != call.id:
        return call
    return ToolCall(call.id, request.name, request.arguments)


def operation_kind(call: ToolCall) -> str:
    """Classify only known resolved operations, with conservative unknowns."""
    if call.name in READ_ONLY_TOOLS:
        return "read"
    if call.name in PROGRESS_TOOLS:
        return "progress"
    return "other"
