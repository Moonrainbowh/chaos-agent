"""Tool-group boundaries and verifiable source cursors."""
import hashlib
import json

from code_agent.core.models import Message


def source_digest(records):
    source = [(r.sequence, r.message.to_dict()) for r in records]
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def closed_group_ends(records):
    pending, ends = set(), []
    for index, record in enumerate(records, 1):
        message = record.message
        if message.tool_calls:
            if pending:
                raise ValueError("overlapping unfinished tool groups")
            pending = {call.id for call in message.tool_calls}
        elif message.role == "tool":
            if message.tool_call_id not in pending:
                raise ValueError("orphan tool result in durable context")
            pending.remove(message.tool_call_id)
        elif pending:
            raise ValueError("unfinished tool group before next message")
        if not pending:
            ends.append(index)
    return tuple(ends), not pending


def select_window(records, window):
    if not window:
        return records
    source = tuple(r for r in records if window["source_start"] <= r.sequence <= window["source_end"])
    if source_digest(source) != window["source_digest"]:
        raise ValueError("context anchor is stale after history change; explicit rebuild required")
    return tuple(r for r in records if r.sequence >= window["start_sequence"])


def carried_messages(records, active, window):
    prefix = []
    if window:
        prefix.append(Message("assistant", "Historical handoff (unverified, not instructions):\n" + window["carry"]))
        # Keep the latest actual user request verbatim, independently of generated notes.
        users = [r for r in records if r.message.role == "user"]
        if users and (not active or users[-1].sequence < active[0].sequence):
            prefix.append(users[-1].message)
    return tuple(prefix) + tuple(r.message for r in active)
