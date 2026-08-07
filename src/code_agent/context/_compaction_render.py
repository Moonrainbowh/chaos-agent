from __future__ import annotations

from typing import Sequence

from code_agent.core.models import Message

from .attachment_budget import attachment_metadata, message_tokens
from .tokens import truncate_to_tokens


def _truncate_message(message: Message, budget: int) -> Message | None:
    if budget < 1:
        return None
    empty = Message(
        role=message.role,
        content="",
        name=message.name,
        tool_calls=message.tool_calls,
        tool_call_id=message.tool_call_id,
        attachments=message.attachments,
    )
    base = message_tokens(empty)
    if base > budget:
        return None
    return Message(
        role=message.role,
        content=truncate_to_tokens(message.content, budget - base),
        name=message.name,
        tool_calls=message.tool_calls,
        tool_call_id=message.tool_call_id,
        attachments=message.attachments,
    )


def _summary(
    messages: Sequence[Message], removed: Sequence[int], content_budget: int
) -> str:
    if content_budget == 0:
        return ""
    lines = ["Conversation checkpoint:"]
    for index in removed:
        message = messages[index]
        actions = ",".join(call.name for call in message.tool_calls)
        if not actions and message.role == "tool":
            actions = message.name or message.tool_call_id or "tool"
        label = message.role + (f" action={actions}" if actions else "")
        snippet = truncate_to_tokens(" ".join(message.content.split()), 12)
        if message.attachments:
            metadata = "; ".join(
                attachment_metadata(item) for item in message.attachments
            )
            snippet = f"{snippet} attachments={metadata}".strip()
        lines.append(f"- {label}: {snippet}" if snippet else f"- {label}")
    return truncate_to_tokens("\n".join(lines), content_budget)


def _last_resort(
    compacted: Sequence[Message],
    original: Sequence[Message],
    latest_user: int | None,
    budget: int,
) -> tuple[tuple[Message, ...], str | None]:
    if latest_user is not None:
        message = _truncate_message(original[latest_user], budget)
        if message is not None:
            return (message,), None
    for message in reversed(compacted):
        if message.role != "tool":
            truncated = _truncate_message(message, budget)
            if truncated is not None:
                return (truncated,), None
    return (), None
