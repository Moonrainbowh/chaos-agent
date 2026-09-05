"""Stable references over the existing durable user/assistant/tool message journal."""
import json
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
    records = tuple(await sessions.load_message_records(thread))
    windows = await sessions.context_records(thread, "window")
    index = window_index(thread, records, windows)
    if operation == "list_windows":
        return page(index, args)
    window = args.get("window_id")
    if window is not None and window not in {w["window_id"] for w in index}:
        if operation == "read_item":
            raise ValueError("history item/window not found in current task")
        return {"items": [], "next_offset": None}
    selected = [r for r in records if window is None or record_window(r, index) == window]
    if operation == "read_item":
        record = next((r for r in selected if item_id(r) == args.get("item_id")), None)
        if record is None:
            raise ValueError("history item/window not found in current task")
        return read_fragment(record, index, args)
    return list_or_search(selected, index, operation, args)


def read_fragment(record, index, args):
    text = json.dumps(record.message.to_dict(), ensure_ascii=False)
    offset = bounded_int(args, "offset", 0, len(text))
    limit = bounded_int(args, "max_chars", 4000, 16000)
    if limit == 0:
        raise ValueError("max_chars must be positive")
    end = min(len(text), offset + limit)
    return {"item_id": item_id(record), "window_id": record_window(record, index),
            "offset": offset, "text": text[offset:end],
            "next_offset": end if end < len(text) else None}


def list_or_search(records, index, operation, args):
    query = args.get("query", "")
    if not isinstance(query, str) or len(query) > 1000:
        raise ValueError("query must be text of at most 1000 characters")
    if operation == "search_contents" and not query:
        raise ValueError("search query cannot be empty")
    if operation not in {"list_items", "search_contents"}:
        raise ValueError("unknown history operation")
    result = []
    for record in records:
        if args.get("role") and args["role"] != record.message.role:
            continue
        text = json.dumps(record.message.to_dict(), ensure_ascii=False)
        # Literal matching preserves exact Unicode offsets and includes tool arguments.
        found = text.find(query) if operation == "search_contents" else 0
        if found < 0:
            continue
        offset = max(0, found - 120)
        result.append({"item_id": item_id(record), "window_id": record_window(record, index),
                       "role": record.message.role, "sequence": record.sequence,
                       "offset": offset, "snippet": text[offset:offset + 600]})
    return page(result, args, maximum=20)
