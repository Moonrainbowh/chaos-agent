from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .config import ApiProtocol, ProviderConfig
from .errors import ProviderConfigError


_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


@dataclass(frozen=True)
class ProviderRequestOptions:
    reasoning_effort: str | None
    max_output_tokens: int


def request_options(
    protocol: ApiProtocol,
    *,
    reasoning_effort: str | None,
    max_output_tokens: int,
) -> ProviderRequestOptions:
    if reasoning_effort is not None and (
        not isinstance(reasoning_effort, str)
        or reasoning_effort not in _REASONING_EFFORTS
    ):
        raise ProviderConfigError("unsupported reasoning effort")
    if (
        isinstance(max_output_tokens, bool)
        or not isinstance(max_output_tokens, int)
        or max_output_tokens <= 0
    ):
        raise ProviderConfigError("max_output_tokens must be a positive integer")
    if (
        protocol is ApiProtocol.ANTHROPIC_MESSAGES
        and reasoning_effort is not None
    ):
        raise ProviderConfigError(
            "reasoning effort is not supported for anthropic_messages"
        )
    return ProviderRequestOptions(reasoning_effort, max_output_tokens)


def chat_payload(
    config: ProviderConfig,
    messages: list[dict[str, object]],
    tools: Sequence[dict[str, object]],
    options: ProviderRequestOptions,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": config.model,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_completion_tokens": options.max_output_tokens,
        "messages": messages,
    }
    if options.reasoning_effort is not None:
        payload["reasoning_effort"] = options.reasoning_effort
    if tools:
        payload["tools"] = list(tools)
    return payload


def responses_payload(
    config: ProviderConfig,
    instructions: str,
    inputs: list[dict[str, object]],
    tools: Sequence[dict[str, object]],
    options: ProviderRequestOptions,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": config.model,
        "stream": True,
        "instructions": instructions,
        "input": inputs,
        "max_output_tokens": options.max_output_tokens,
    }
    if options.reasoning_effort is not None:
        payload["reasoning"] = {"effort": options.reasoning_effort}
    if tools:
        payload["tools"] = list(tools)
    return payload


def anthropic_payload(
    config: ProviderConfig,
    system: str,
    messages: list[dict[str, object]],
    tools: Sequence[dict[str, object]],
    options: ProviderRequestOptions,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": config.model,
        "max_tokens": options.max_output_tokens,
        "stream": True,
        "system": system,
        "messages": messages,
    }
    if tools:
        payload["tools"] = list(tools)
    return payload
