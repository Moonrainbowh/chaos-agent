"""Stable references over the existing durable user/assistant/tool message journal."""
import uuid


def first_window_id(thread):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"chaos:window:{thread}:initial").hex


def item_id(record):
    return uuid.uuid5(uuid.NAMESPACE_URL,
                      f"chaos:item:{record.thread_id}:{record.sequence}").hex


def window_index(thread, records, windows):
    start = records[0].sequence if records else 1
    identifier = first_window_id(thread)
    result = []
    for window in windows:
        result.append({"window_id": identifier, "start": start,
                       "end": window["start_sequence"] - 1})
        identifier, start = window["id"], window["start_sequence"]
    result.append({"window_id": identifier, "start": start,
                   "end": records[-1].sequence if records else 0})
    return result


def record_window(record, index):
    return next(w["window_id"] for w in index if w["start"] <= record.sequence <= w["end"])


def bounded_int(args, key, default, maximum):
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"{key} must be an integer in 0..{maximum}")
    return value


def page(items, args, maximum=50):
    offset = bounded_int(args, "offset", 0, 2**31 - 1)
    limit = bounded_int(args, "limit", 10, maximum)
    if limit == 0:
        raise ValueError("limit must be positive")
    end = offset + limit
    return {"items": items[offset:end], "next_offset": end if end < len(items) else None}


async def history_action(sessions, thread, operation, args):
    if operation == "list_windows":
        offset = bounded_int(args, "offset", 0, 2**31 - 1)
        limit = bounded_int(args, "limit", 10, 50)
        if not limit:
            raise ValueError("limit must be positive")
        return await sessions.context_window_page(thread, offset=offset, limit=limit)
    window = args.get("window_id")
    bounds = await sessions.context_window_bounds(thread, window) if window is not None else None
    if window is not None and bounds is None:
        if operation == "read_item":
            raise ValueError("history item/window not found in current task")
        return {"items": [], "next_offset": None}
    if operation == "read_item":
        offset = bounded_int(args, "offset", 0, 2**31 - 1)
        maximum = bounded_int(args, "max_chars", 4000, 16000)
        if not maximum:
            raise ValueError("max_chars must be positive")
        fragment = await sessions.history_item_fragment(thread, args.get("item_id"), offset=offset, max_chars=maximum)
        if fragment is None or (bounds and not bounds["start"] <= fragment["sequence"] <= bounds["end"]):
            raise ValueError("history item/window not found in current task")
        owner = await sessions.context_record_for_sequence(thread, fragment["sequence"])
        return {"item_id": fragment["item_id"], "window_id": owner["id"] if owner else first_window_id(thread),
                "offset": offset, "text": fragment["text"], "next_offset": fragment["next_offset"]}
    query = args.get("query", "")
    if not isinstance(query, str) or len(query) > 1000:
        raise ValueError("query must be text of at most 1000 characters")
    if operation == "search_contents" and not query:
        raise ValueError("search query cannot be empty")
    if operation not in {"list_items", "search_contents"}:
        raise ValueError("unknown history operation")
    offset = bounded_int(args, "offset", 0, 2**31 - 1)
    limit = bounded_int(args, "limit", 10, 20)
    if not limit:
        raise ValueError("limit must be positive")
    result = await sessions.search_history_page(
        thread, query if operation == "search_contents" else "",
        after_sequence=max(0, bounds["start"] - 1) if bounds else 0,
        before_sequence=bounds["end"] + 1 if bounds else None,
        role=args.get("role") or None, limit=limit, offset=offset)
    items = []
    for item in result["items"]:
        owner = await sessions.context_record_for_sequence(thread, item["sequence"])
        items.append({**item, "window_id": owner["id"] if owner else first_window_id(thread)})
    return {"items": items, "next_offset": result["next_offset"]}
