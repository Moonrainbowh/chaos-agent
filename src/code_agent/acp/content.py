from __future__ import annotations

from collections.abc import Sequence

import acp.schema as schema
from acp import RequestError, update_agent_message_text, update_user_message_text

from code_agent.core.models import Message


_MAX_PROMPT_CHARS = 200_000


def prompt_text(blocks: Sequence[object]) -> str:
    if isinstance(blocks, (str, bytes, bytearray)):
        raise RequestError.invalid_params({"prompt": "must be a content block list"})
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, schema.TextContentBlock):
            parts.append(block.text)
        elif isinstance(block, schema.ResourceContentBlock):
            parts.append(f"[Resource link: {block.name}] {block.uri}")
        else:
            raise RequestError.invalid_params(
                {"prompt": f"unsupported content type: {type(block).__name__}"}
            )
    text = "\n\n".join(part for part in parts if part.strip())
    if not text.strip():
        raise RequestError.invalid_params({"prompt": "must contain text or a resource link"})
    if len(text) > _MAX_PROMPT_CHARS:
        raise RequestError.invalid_params(
            {"prompt": f"must not exceed {_MAX_PROMPT_CHARS} characters"}
        )
    return text


def history_update(message: Message) -> object | None:
    if not message.content:
        return None
    if message.role == "user":
        return update_user_message_text(message.content)
    if message.role == "assistant":
        return update_agent_message_text(message.content)
    return None


__all__ = ("history_update", "prompt_text")
