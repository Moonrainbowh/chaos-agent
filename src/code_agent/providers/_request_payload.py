from __future__ import annotations

from collections.abc import Sequence

from .config import ProviderConfig


def chat_payload(
    config: ProviderConfig,
    messages: list[dict[str, object]],
    tools: Sequence[dict[str, object]],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": config.model,
        "stream": True,
        "stream_options": {"include_usage": True},
        "messages": messages,
    }
    if tools:
        payload["tools"] = list(tools)
    return payload


def responses_payload(
    config: ProviderConfig,
    instructions: str,
    inputs: list[dict[str, object]],
    tools: Sequence[dict[str, object]],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": config.model,
        "stream": True,
        "instructions": instructions,
        "input": inputs,
    }
    if tools:
        payload["tools"] = list(tools)
    return payload


def anthropic_payload(
    config: ProviderConfig,
    system: str,
    messages: list[dict[str, object]],
    tools: Sequence[dict[str, object]],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": config.model,
        "max_tokens": 4096,
        "stream": True,
        "system": system,
        "messages": messages,
    }
    if tools:
        payload["tools"] = list(tools)
    return payload
