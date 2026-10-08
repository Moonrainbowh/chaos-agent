"""Tool-group boundaries and verifiable source cursors."""
import hashlib
import json

from code_agent.core.models import Message


class ToolGroupCursor:
    """Constant-size state for one assistant/result group while streaming a range."""
    def __init__(self):
        self.pending = {}

    def observe(self, message):
        if message.tool_calls:
            if self.pending:
                raise ValueError("overlapping unfinished tool groups")
            ids = tuple(call.id for call in message.tool_calls)
            if len(set(ids)) != len(ids):
                raise ValueError("duplicate tool call ids in context source")
            self.pending = {call.id: call.name for call in message.tool_calls}
        elif message.role == "tool":
            if message.tool_call_id not in self.pending:
                raise ValueError("orphan tool result in durable context")
            if message.name != self.pending[message.tool_call_id]:
                raise ValueError("tool result name does not match source call; pending action remains")
            del self.pending[message.tool_call_id]
        elif self.pending:
            raise ValueError("unfinished tool group before next message")

    def require_closed(self):
        if self.pending:
            raise ValueError("context source ends in an unfinished tool-call group")


def source_digest(records):
    source = [(r.sequence, r.message.to_dict()) for r in records]
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def closed_group_ends(records):
    group, ends = ToolGroupCursor(), []
    for index, record in enumerate(records, 1):
        group.observe(record.message)
        if not group.pending:
            ends.append(index)
    return tuple(ends), not group.pending


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
