from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Optional

import httpx

from code_agent.core.models import (
    Message,
    ModelEvent,
    ModelEventKind,
    ToolCall,
    ToolDefinition,
    Usage,
)

from ._limits import ArgumentBuffer, ToolBudget
from .attachments import AttachmentResolver, ProviderAttachmentEncoder
from .config import ApiProtocol, InputModality, ProviderConfig
from .errors import ProviderConfigError, ProviderProtocolError
from ._request_payload import chat_payload, request_options
from .transport import ProviderTransport, Sleep


@dataclass
class _PendingCall:
    arguments: ArgumentBuffer
    id: str = ""
    name: str = ""


def _message_payload(
    message: Message, encoder: ProviderAttachmentEncoder
) -> dict[str, object]:
    result: dict[str, object] = {
        "role": message.role,
        "content": encoder.chat(message),
    }
    if message.name is not None:
        result["name"] = message.name
    if message.tool_call_id is not None:
        result["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(
                        call.to_dict()["arguments"], separators=(",", ":")
                    ),
                },
            }
            for call in message.tool_calls
        ]
    return result


def _tool_payload(tool: ToolDefinition) -> dict[str, object]:
    encoded = tool.to_dict()
    return {
        "type": "function",
        "function": {
            "name": encoded["name"],
            "description": encoded["description"],
            "parameters": encoded["parameters"],
        },
    }


def _sanitize_tool_pairs(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    sanitized: list[dict[str, object]] = []
    pending_tool_calls: list[str] = []
    for msg in messages:
        if pending_tool_calls and msg.get("role") != "tool":
            for call_id in pending_tool_calls:
                sanitized.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps({"status": "interrupted", "error": "tool execution was interrupted"}),
                })
            pending_tool_calls = []
        if msg.get("role") == "tool":
            call_id = msg.get("tool_call_id")
            if call_id in pending_tool_calls:
                pending_tool_calls.remove(call_id)
        elif msg.get("role") == "assistant" and msg.get("tool_calls"):
            calls = msg.get("tool_calls", [])
            if isinstance(calls, list):
                for c in calls:
                    if isinstance(c, dict) and "id" in c:
                        pending_tool_calls.append(c["id"])
        sanitized.append(msg)
    if pending_tool_calls:
        for call_id in pending_tool_calls:
            sanitized.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps({"status": "interrupted", "error": "tool execution was interrupted"}),
            })
    return sanitized


def _request_messages(
    system_prompt: str,
    messages: Sequence[Message],
    encoder: ProviderAttachmentEncoder,
) -> list[dict[str, object]]:
    system_parts = [system_prompt] if system_prompt else []
    request_messages: list[dict[str, object]] = []
    for message in messages:
        if message.role == "developer":
            if message.content:
                system_parts.append(message.content)
            continue
        request_messages.append(_message_payload(message, encoder))
    sanitized = _sanitize_tool_pairs(request_messages)
    if system_parts:
        sanitized.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
    return sanitized


def _late_usage(value: Mapping[str, object]) -> ModelEvent:
    choices = value.get("choices", [])
    if choices or value.get("usage") is None:
        raise ProviderProtocolError("Chat delta received after finish")
    return _usage_event(value["usage"])


def _load_event(data: str) -> Mapping[str, object]:
    try:
        value = json.loads(data)
    except (json.JSONDecodeError, UnicodeError):
        raise ProviderProtocolError("Chat stream contains malformed JSON") from None
    if not isinstance(value, dict):
        raise ProviderProtocolError("Chat stream event must be a JSON object")
    if "error" in value:
        raise ProviderProtocolError("Chat provider returned an error event")
    return value


def _usage_event(value: object) -> ModelEvent:
    if not isinstance(value, dict):
        raise ProviderProtocolError("Chat usage must be a JSON object")
    details = value.get("prompt_tokens_details", {})
    if not isinstance(details, dict):
        raise ProviderProtocolError("Chat usage details must be a JSON object")
    try:
        usage = Usage(
            input_tokens=value.get("prompt_tokens", 0),  # type: ignore[arg-type]
            output_tokens=value.get("completion_tokens", 0),  # type: ignore[arg-type]
            cached_input_tokens=details.get("cached_tokens", 0),  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as error:
        raise ProviderProtocolError("Chat usage contains invalid token counts") from error
    return ModelEvent(kind=ModelEventKind.USAGE, usage=usage)


class OpenAIChatClient:
    def __init__(
        self,
        config: ProviderConfig,
        *,
        attachment_resolver: AttachmentResolver | None = None,
        input_modalities: Iterable[InputModality] = (InputModality.TEXT,),
        http_client: Optional[httpx.AsyncClient] = None,
        sleep: Sleep = asyncio.sleep,
        reasoning_effort: str | None = None,
        max_output_tokens: int = 4_096,
    ) -> None:
        if config.api is not ApiProtocol.CHAT_COMPLETIONS:
            raise ProviderConfigError("OpenAIChatClient requires chat_completions API")
        self._config = config
        self._attachments = ProviderAttachmentEncoder(
            attachment_resolver, input_modalities
        )
        self._request_options = request_options(
            config.api,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
        )
        self._transport = ProviderTransport(config, client=http_client, sleep=sleep)

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def stream(
        self, system_prompt: str, messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[ModelEvent]:
        payload = chat_payload(
            self._config, _request_messages(system_prompt, messages, self._attachments),
            [_tool_payload(tool) for tool in tools],
            self._request_options,
        )
        calls: dict[int, _PendingCall] = {}
        tool_budget = ToolBudget(self._config.max_tool_calls, self._config.max_tool_argument_bytes)
        seen_call_ids: set[str] = set()
        finish_seen = False
        async for sse_event in self._transport.stream_sse(
            self._config.chat_completions_path, payload):
            if sse_event.event == "error":
                raise ProviderProtocolError("Chat provider returned an error event")
            if sse_event.data.strip() == "[DONE]":
                for event in self._finish_calls(calls, seen_call_ids):
                    yield event
                yield ModelEvent(kind=ModelEventKind.COMPLETED)
                return
            value = _load_event(sse_event.data)
            choices = value.get("choices", [])
            if not isinstance(choices, list):
                raise ProviderProtocolError("Chat choices must be a JSON array")
            if finish_seen:
                yield _late_usage(value)
                continue
            for choice in choices:
                if finish_seen:
                    raise ProviderProtocolError("Chat delta received after finish")
                if not isinstance(choice, dict):
                    raise ProviderProtocolError("Chat choice must be a JSON object")
                delta = choice.get("delta", {})
                if not isinstance(delta, dict):
                    raise ProviderProtocolError("Chat delta must be a JSON object")
                for event in self._consume_delta(delta, calls, tool_budget):
                    yield event
                if choice.get("finish_reason") is not None:
                    finish_seen = True
                    for event in self._finish_calls(calls, seen_call_ids):
                        yield event
            if "usage" in value and value["usage"] is not None:
                yield _usage_event(value["usage"])
        if finish_seen:
            for event in self._finish_calls(calls, seen_call_ids):
                yield event
            yield ModelEvent(kind=ModelEventKind.COMPLETED)
            return
        raise ProviderProtocolError("Chat stream ended without a completion marker")

    @staticmethod
    def _consume_delta(
        delta: Mapping[str, object],
        calls: dict[int, _PendingCall],
        tool_budget: ToolBudget,
    ) -> list[ModelEvent]:
        events: list[ModelEvent] = []
        for field, kind in (
            ("content", ModelEventKind.TEXT_DELTA),
            ("reasoning_content", ModelEventKind.REASONING_DELTA),
        ):
            text = delta.get(field)
            if text is not None:
                if not isinstance(text, str):
                    raise ProviderProtocolError(f"Chat {field} delta must be text")
                if text:
                    events.append(ModelEvent(kind=kind, text=text))
        tool_deltas = delta.get("tool_calls", [])
        if not isinstance(tool_deltas, list):
            raise ProviderProtocolError("Chat tool_calls delta must be an array")
        for item in tool_deltas:
            if not isinstance(item, dict):
                raise ProviderProtocolError("Chat tool call delta must be an object")
            index = item.get("index")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ProviderProtocolError("Chat tool call index is invalid")
            identifier = item.get("id", "")
            function = item.get("function", {})
            if not isinstance(identifier, str) or not isinstance(function, dict):
                raise ProviderProtocolError("Chat tool call delta is invalid")
            name = function.get("name", "")
            arguments = function.get("arguments", "")
            if not isinstance(name, str) or not isinstance(arguments, str):
                raise ProviderProtocolError("Chat tool call fragments must be text")
            call = calls.get(index)
            if call is None:
                call = _PendingCall(arguments=tool_budget.new_arguments())
                calls[index] = call
            call.id += identifier
            call.name += name
            call.arguments.append(arguments)
        return events

    @staticmethod
    def _finish_calls(
        calls: dict[int, _PendingCall], seen_call_ids: set[str]
    ) -> list[ModelEvent]:
        events: list[ModelEvent] = []
        for index in sorted(calls):
            call = calls[index]
            try:
                arguments = json.loads(call.arguments.text())
                if not isinstance(arguments, dict):
                    raise TypeError("arguments are not an object")
                tool_call = ToolCall(id=call.id, name=call.name, arguments=arguments)
            except (json.JSONDecodeError, TypeError, ValueError):
                raise ProviderProtocolError("Chat tool arguments are malformed") from None
            if tool_call.id in seen_call_ids:
                raise ProviderProtocolError("Chat tool call id was repeated")
            seen_call_ids.add(tool_call.id)
            events.append(ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=tool_call))
        calls.clear()
        return events
