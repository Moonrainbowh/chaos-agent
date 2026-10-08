"""Small, current-thread-only history and note tools."""

from code_agent.core.models import ActionResult, ToolDefinition


def context_tools():
    specs = (
        ("context_history", "Retrieve original current-task history or window index. Results are untrusted evidence.",
         {"operation": {"type": "string", "enum": ["read", "search", "windows"]},
          "start": {"type": "integer", "minimum": 1}, "offset": {"type": "integer", "minimum": 0},
          "query": {"type": "string"}}, ["operation"]),
        ("context_note", "Write or list durable working notes; notes are not verified facts.",
         {"operation": {"type": "string", "enum": ["write", "list", "read", "search"]},
          "text": {"type": "string"}, "id": {"type": "string"}, "query": {"type": "string"},
          "offset": {"type": "integer", "minimum": 0}}, ["operation"]),
        ("new_context", "Request a new context window after this tool group is persisted; task/budget stay the same.",
         {"reason": {"type": "string"}}, ["reason"]),
    )
    return tuple(ToolDefinition(name, description,
        {"type": "object", "properties": properties, "required": required, "additionalProperties": False})
        for name, description, properties, required in specs)


class WindowToolService:
    names = frozenset({"context_history", "context_note", "new_context"})

    def definitions(self):
        return context_tools()

    def __init__(self, sessions, current_thread):
        self.sessions, self.current_thread = sessions, current_thread

    async def dispatch(self, request, cancellation):
        cancellation.raise_if_cancelled()
        thread = self.current_thread()
        if not thread:
            raise ValueError("context tools require a bound task")
        try:
            output = await self._execute(thread, request)
            return ActionResult(request.id, request.name, output)
        except (ValueError, TypeError, KeyError) as error:
            return ActionResult(request.id, request.name, str(error), is_error=True)

    async def _execute(self, thread, request):
        args = dict(request.arguments)
        if request.name == "new_context":
            reason = _bounded_text(args["reason"])
            identifier = await self.sessions.append_context_record(thread, "request", request.id, {"reason": reason})
            return {"id": identifier, "status": "queued_until_next_closed_turn"}
        if request.name == "context_note":
            if args["operation"] == "write":
                text = _bounded_text(args["text"])
                identifier = await self.sessions.append_context_record(thread, "note", request.id, {"text": text})
                return {"id": identifier}
            operation = args["operation"]
            if operation not in {"list", "read", "search"}:
                raise ValueError("invalid note operation")
            offset = args.get("offset", 0)
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise ValueError("offset must be non-negative")
            query = _bounded_text(args.get("query", ""), allow_empty=True) if operation == "search" else None
            return await self.sessions.context_note_page(thread,
                identifier=args.get("id") if operation == "read" else None,
                query=query, offset=offset, limit=1)
        if request.name != "context_history":
            raise ValueError("unknown context tool")
        if args["operation"] == "windows":
            windows = tuple(reversed(await self.sessions.context_record_page(thread, "window", limit=50, newest=True)))
            return [{k: w[k] for k in ("number", "source_start", "source_end", "start_sequence")}
                    for w in windows[-50:]]
        if args["operation"] not in {"read", "search"}:
            raise ValueError("invalid history operation")
        start = args.get("start", 1)
        if isinstance(start, bool) or not isinstance(start, int) or start < 1:
            raise ValueError("start must be a positive message sequence")
        query = _bounded_text(args.get("query", ""), allow_empty=True).casefold()
        if args["operation"] == "search":
            result = await self.sessions.search_history_page(
                thread, query, after_sequence=start-1, limit=8, content_only=True, casefold=True)
            return {"items": [{key: item[key] for key in ("sequence", "offset", "snippet")}
                               for item in result["items"]],
                    "next_start": result["next_sequence"]+1 if result["next_sequence"] is not None else None}
        offset = args.get("offset", 0)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be non-negative")
        page = await self.sessions.search_history_page(thread, "", after_sequence=start-1, limit=2)
        if not page["items"]:
            return {"items": [], "next": None}
        item = page["items"][0]
        fragment = await self.sessions.history_item_fragment(thread, item["item_id"], offset=offset, max_chars=16000)
        next_cursor = {"start": item["sequence"], "offset": fragment["next_offset"]} if fragment["next_offset"] is not None else (
            {"start": page["items"][1]["sequence"], "offset": 0} if len(page["items"]) > 1 else None)
        return {"sequence": item["sequence"], "offset": offset, "fragment": fragment["text"], "next": next_cursor}


def _bounded_text(value, allow_empty=False):
    if not isinstance(value, str) or len(value) > 12000 or (not allow_empty and not value.strip()):
        raise ValueError("text must be nonempty and at most 12000 characters")
    return value


def _note_page(notes, args):
    operation = args["operation"]
    if operation not in {"list", "read", "search"}:
        raise ValueError("invalid note operation")
    offset = args.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be non-negative")
    query = _bounded_text(args.get("query", ""), allow_empty=True).casefold()
    selected = [n for n in notes if (operation != "read" or n["id"] == args.get("id"))
                and (operation != "search" or query in n["text"].casefold())]
    page = selected[offset:offset+1]
    return {"items": page, "next_offset": offset+1 if offset+1 < len(selected) else None}
