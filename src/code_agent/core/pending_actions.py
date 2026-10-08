"""Detect unmatched chronological calls without trusting task status labels."""
from collections import Counter
from collections.abc import Sequence
from .models import Message, ToolCall


def pending_calls(messages: Sequence[Message]) -> tuple[ToolCall, ...]:
    counts = Counter(call.id for message in messages for call in message.tool_calls)
    pending = []
    for message in messages:
        if message.role == 'assistant':
            pending.extend(message.tool_calls)
        elif message.role == 'tool':
            pending = [call for call in pending if not (
                counts[call.id] == 1 and call.id == message.tool_call_id
                and call.name == message.name)]
    return tuple(pending)
