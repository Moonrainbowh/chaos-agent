from __future__ import annotations

import json
from math import ceil

from code_agent.core.attachments import AttachmentRef
from code_agent.core.models import Message

from .tokens import estimate_tokens


def estimate_attachment_tokens(reference: AttachmentRef) -> int:
    """Return a conservative provider-independent attachment token estimate."""
    if not isinstance(reference, AttachmentRef):
        raise TypeError("reference must be an AttachmentRef")
    if reference.media_type == "text/plain":
        return max(1, ceil(reference.size_bytes / 2))
    assert reference.width is not None and reference.height is not None
    tiles = ceil(reference.width / 512) * ceil(reference.height / 512)
    return 85 + 170 * tiles


def attachment_metadata(reference: AttachmentRef) -> str:
    return reference.summary()


def message_tokens(message: Message) -> int:
    total = 1 + estimate_tokens(message.content)
    total += sum(estimate_attachment_tokens(item) for item in message.attachments)
    if message.name:
        total += estimate_tokens(message.name)
    if message.tool_call_id:
        total += estimate_tokens(message.tool_call_id)
    for call in message.tool_calls:
        total += 1 + estimate_tokens(
            json.dumps(
                call.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return total
