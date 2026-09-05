"""Bounded projections of task-local, versioned note files."""
from .persistent_history import bounded_int, page


async def notes_action(sessions, thread, key, operation, args):
    if operation in {"write_file", "append_to_file"}:
        return await sessions.write_context_note(thread, key, args["path"], args["text"],
                                                 append=operation == "append_to_file")
    notes = await sessions.context_note_files(thread)
    if operation == "read_file":
        path = args["path"]
        note = next((n for n in notes if n["path"] == path), None)
        if note is None:
            raise ValueError("note file not found in current task")
        return read_note(note, args)
    prefix = args.get("prefix", "")
    if not isinstance(prefix, str) or len(prefix) > 512:
        raise ValueError("prefix must contain at most 512 characters")
    query = args.get("query", "")
    if not isinstance(query, str) or len(query) > 1000:
        raise ValueError("query must contain at most 1000 characters")
    if operation == "search_contents" and not query:
        raise ValueError("search query cannot be empty")
    if operation not in {"list_files", "search_contents"}:
        raise ValueError("unknown notes operation")
    result = []
    for note in notes:
        if not note["path"].startswith(prefix):
            continue
        row = {"path": note["path"], "revision": note["revision"],
               "bytes": len(note["content"].encode("utf-8"))}
        if operation == "search_contents":
            found = note["content"].find(query)
            if found < 0:
                continue
            row.update(offset=max(0, found - 120),
                       snippet=note["content"][max(0, found - 120):found + 480])
        result.append(row)
    return page(result, args, maximum=20)


def read_note(note, args):
    content = note["content"]
    offset = bounded_int(args, "offset", 0, len(content))
    limit = bounded_int(args, "max_chars", 4000, 16000)
    if not limit:
        raise ValueError("max_chars must be positive")
    end = min(len(content), offset + limit)
    return {"path": note["path"], "revision": note["revision"], "offset": offset,
            "text": content[offset:end], "next_offset": end if end < len(content) else None}
