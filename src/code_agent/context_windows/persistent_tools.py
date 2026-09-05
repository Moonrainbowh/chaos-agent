"""Flat API-compatible names for Codex-style history, notes, and window controls."""
from code_agent.core.models import ActionResult, ToolDefinition
from .persistent_history import history_action
from .persistent_notes import notes_action
from .tool_names import HISTORY_TOOLS, NOTE_READ_TOOLS, NOTE_WRITE_TOOLS, PERSISTENT_TOOLS


def persistent_tools():
    text = {"type": "string"}
    integer = {"type": "integer", "minimum": 0}
    paging = {"offset": integer, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}
    fragment = {"offset": integer, "max_chars": {"type": "integer", "minimum": 1, "maximum": 16000}}
    filters = {"window_id": text, "role": {"type": "string", "enum": ["user", "assistant", "tool", "system"]}}
    specs = (
        ("history_list_windows", "List durable windows of the current task.", paging, []),
        ("history_list_items", "List message references; use read_item for exact content.", {**paging, **filters}, []),
        ("history_read_item", "Read original message JSON including tool calls/results; paginate until next_offset is null.",
         {**fragment, "window_id": text, "item_id": text}, ["item_id"]),
        ("history_search_contents", "Literal case-sensitive search of persisted messages and tool arguments/results.",
         {**paging, **filters, "query": text}, ["query"]),
        ("notes_list_files", "List current-task note paths and revisions; content is not auto-injected.",
         {**paging, "prefix": text}, []),
        ("notes_read_file", "Read a current-task virtual note, with bounded character pagination.",
         {**fragment, "path": text}, ["path"]),
        ("notes_search_contents", "Literal case-sensitive search of latest current-task note versions.",
         {**paging, "prefix": text, "query": text}, ["query"]),
        ("notes_write_file", "Create or replace a virtual note; preserve constraints and history IDs. Not a workspace file.",
         {"path": text, "text": text}, ["path", "text"]),
        ("notes_append_to_file", "Atomically append to a virtual note (create if missing).",
         {"path": text, "text": text}, ["path", "text"]),
        ("new_context", "After saving notes, request a fresh window without summarization. Effective after the complete tool group.",
         {"reason": text}, []),
        ("get_context_remaining", "Read the latest built request's remaining input estimate, not cumulative task budget.", {}, []),
    )
    return tuple(ToolDefinition(name, description,
        {"type": "object", "properties": properties, "required": required, "additionalProperties": False})
        for name, description, properties, required in specs)


class PersistentToolService:
    names = frozenset(PERSISTENT_TOOLS)

    def __init__(self, sessions, current_thread, builder):
        self.sessions, self.current_thread, self.builder = sessions, current_thread, builder

    def definitions(self):
        return persistent_tools()

    async def dispatch(self, request, cancellation):
        cancellation.raise_if_cancelled()
        thread = self.current_thread()
        if not thread:
            raise ValueError("context tools require a bound task")
        try:
            validate_arguments(request)
            result = await self._execute(thread, request)
            return ActionResult(request.id, request.name, result)
        except (ValueError, TypeError, KeyError) as error:
            return ActionResult(request.id, request.name, str(error), is_error=True)

    async def _execute(self, thread, request):
        args = dict(request.arguments)
        if request.name in HISTORY_TOOLS:
            return await history_action(self.sessions, thread, request.name[8:], args)
        if request.name in NOTE_READ_TOOLS + NOTE_WRITE_TOOLS:
            return await notes_action(self.sessions, thread, request.id, request.name[6:], args)
        if request.name == "get_context_remaining":
            return getattr(self.builder, "remaining_by_thread", {}).get(
                thread, {"context_tokens_remaining": None, "estimate": True})
        if request.name != "new_context":
            raise ValueError("unknown persistent context tool")
        reason = args.get("reason", "model requested fresh context")
        if not isinstance(reason, str) or len(reason) > 1000:
            raise ValueError("reason must contain at most 1000 characters")
        identifier = await self.sessions.append_context_record(
            thread, "request", request.id, {"reason": reason})
        return {"id": identifier, "status": "queued_until_next_closed_turn", "summarization": False}


def validate_arguments(request):
    """Enforce flat schemas even when a provider ignores additionalProperties."""
    definition = next((tool for tool in persistent_tools() if tool.name == request.name), None)
    if definition is None:
        raise ValueError("unknown persistent context tool")
    schema = definition.parameters
    properties = schema["properties"]
    args = request.arguments
    if set(args) - set(properties) or set(schema["required"]) - set(args):
        raise ValueError("unexpected or missing persistent context argument")
    for key, value in args.items():
        spec = properties[key]
        if spec["type"] == "string" and not isinstance(value, str):
            raise ValueError(f"{key} must be text")
        if spec["type"] == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} must be an integer")
            if value < spec.get("minimum", 0) or value > spec.get("maximum", 2**31 - 1):
                raise ValueError(f"{key} is outside the supported range")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"{key} has an unsupported value")
