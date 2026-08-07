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

from ._limits import ArgumentBuffer, ToolBudget, ensure_utf8_limit
from .attachments import AttachmentResolver, ProviderAttachmentEncoder
from .config import ApiProtocol, InputModality, ProviderConfig
from .errors import ProviderConfigError, ProviderProtocolError
from ._request_payload import anthropic_payload
from .transport import ProviderTransport, Sleep

def _request_messages(
    system_prompt: str,
    messages: Sequence[Message],
    encoder: ProviderAttachmentEncoder,
) -> tuple[str, list[dict[str, object]]]:
    system_parts = [system_prompt] if system_prompt else []
    result: list[dict[str, object]] = []
    for message in messages:
        if message.role in {"system", "developer"}:
            if message.content:
                system_parts.append(message.content)
            continue
        if message.role == "tool":
            if message.tool_call_id is None:
                raise ProviderConfigError("Anthropic tool messages require tool_call_id")
            result.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": message.content,
                        }
                    ],
                }
            )
            continue
        if message.tool_calls:
            encoded = encoder.anthropic(message)
            blocks = (
                list(encoded)
                if isinstance(encoded, list)
                else ([{"type": "text", "text": encoded}] if encoded else [])
            )
            blocks.extend(
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": call.to_dict()["arguments"],
                }
                for call in message.tool_calls
            )
            result.append({"role": message.role, "content": blocks})
        else:
            result.append(
                {"role": message.role, "content": encoder.anthropic(message)}
            )
    return "\n\n".join(system_parts), result


def _request_tool(tool: ToolDefinition) -> dict[str, object]:
    value = tool.to_dict()
    return {
        "name": value["name"],
        "description": value["description"],
        "input_schema": value["parameters"],
    }


def _load_event(data: str) -> Mapping[str, object]:
    try:
        value = json.loads(data)
    except (json.JSONDecodeError, UnicodeError):
        raise ProviderProtocolError("Anthropic stream contains malformed JSON") from None
    if not isinstance(value, dict):
        raise ProviderProtocolError("Anthropic event must be a JSON object")
    return value


@dataclass
class _PendingTool:
    id: str
    name: str
    initial_input: Mapping[str, object]
    fragments: ArgumentBuffer
    emitted: bool = False


def _finish_tool(tool: _PendingTool) -> Optional[ModelEvent]:
    if tool.emitted:
        return None
    try:
        if not tool.fragments.is_empty:
            arguments = json.loads(tool.fragments.text())
            if not isinstance(arguments, dict):
                raise TypeError("tool input is not an object")
        else:
            arguments = dict(tool.initial_input)
        call = ToolCall(id=tool.id, name=tool.name, arguments=arguments)
    except (json.JSONDecodeError, TypeError, ValueError):
        raise ProviderProtocolError("Anthropic tool input is malformed") from None
    tool.emitted = True
    return ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=call)


class _UsageState:
    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_input_tokens = 0

    def update(self, value: object) -> ModelEvent:
        if not isinstance(value, dict):
            raise ProviderProtocolError("Anthropic usage must be a JSON object")
        fields = (
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("cache_read_input_tokens", "cached_input_tokens"),
        )
        for source, target in fields:
            if source in value:
                count = value[source]
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise ProviderProtocolError("Anthropic usage has invalid token counts")
                setattr(self, target, count)
        return ModelEvent(
            kind=ModelEventKind.USAGE,
            usage=Usage(
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                cached_input_tokens=self.cached_input_tokens,
            ),
        )


class AnthropicClient:
    def __init__(
        self,
        config: ProviderConfig,
        *,
        attachment_resolver: AttachmentResolver | None = None,
        input_modalities: Iterable[InputModality] = (InputModality.TEXT,),
        http_client: Optional[httpx.AsyncClient] = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if config.api is not ApiProtocol.ANTHROPIC_MESSAGES:
            raise ProviderConfigError("AnthropicClient requires anthropic_messages API")
        self._config = config
        self._attachments = ProviderAttachmentEncoder(
            attachment_resolver, input_modalities
        )
        self._transport = ProviderTransport(config, client=http_client, sleep=sleep)

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def stream(self, system_prompt: str, messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[ModelEvent]:
        system, request_messages = _request_messages(system_prompt, messages, self._attachments)
        payload = anthropic_payload(
            self._config, system, request_messages,
            [_request_tool(tool) for tool in tools],
        )
        pending: dict[int, _PendingTool] = {}
        tool_budget = ToolBudget(self._config.max_tool_calls, self._config.max_tool_argument_bytes)
        usage = _UsageState()
        async for sse in self._transport.stream_sse(
            self._config.anthropic_messages_path, payload,
            {"anthropic-version": "2023-06-01"}, auth_header="x-api-key", auth_scheme=None,
        ):
            value = _load_event(sse.data)
            event_type = value.get("type", sse.event)
            if not isinstance(event_type, str):
                continue
            if event_type == "error" or sse.event == "error":
                raise ProviderProtocolError("Anthropic provider returned an error event")
            if event_type == "message_start":
                message = value.get("message")
                if not isinstance(message, dict):
                    raise ProviderProtocolError("Anthropic message_start is invalid")
                if "usage" in message:
                    yield usage.update(message["usage"])
            elif event_type == "content_block_start":
                self._start_block(value, pending, tool_budget)
            elif event_type == "content_block_delta":
                result = self._consume_delta(value, pending)
                if result is not None:
                    yield result
            elif event_type == "content_block_stop":
                index = self._index(value)
                if index in pending:
                    result = _finish_tool(pending[index])
                    if result is not None:
                        yield result
            elif event_type == "message_delta":
                if "usage" in value:
                    yield usage.update(value["usage"])
            elif event_type == "message_stop":
                for index in sorted(pending):
                    result = _finish_tool(pending[index])
                    if result is not None:
                        yield result
                yield ModelEvent(kind=ModelEventKind.COMPLETED)
                return
        raise ProviderProtocolError("Anthropic stream ended without message_stop")
    @classmethod
    def _start_block(
        cls,
        value: Mapping[str, object],
        pending: dict[int, _PendingTool],
        tool_budget: ToolBudget,
    ) -> None:
        block = value.get("content_block")
        if not isinstance(block, dict):
            raise ProviderProtocolError("Anthropic content block must be an object")
        if block.get("type") != "tool_use":
            return
        index = cls._index(value)
        identifier = block.get("id")
        name = block.get("name")
        initial = block.get("input", {})
        if not isinstance(identifier, str) or not isinstance(name, str):
            raise ProviderProtocolError("Anthropic tool identity must be text")
        if not isinstance(initial, dict):
            raise ProviderProtocolError("Anthropic initial tool input must be an object")
        if index in pending:
            raise ProviderProtocolError("Anthropic tool block index is duplicated")
        initial_json = json.dumps(initial, ensure_ascii=False, separators=(",", ":"))
        ensure_utf8_limit(initial_json, tool_budget.max_argument_bytes)
        pending[index] = _PendingTool(
            identifier, name, initial, tool_budget.new_arguments()
        )

    @classmethod
    def _consume_delta(
        cls, value: Mapping[str, object], pending: dict[int, _PendingTool]
    ) -> Optional[ModelEvent]:
        delta = value.get("delta")
        if not isinstance(delta, dict):
            raise ProviderProtocolError("Anthropic content delta must be an object")
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            text = delta.get("text")
            if not isinstance(text, str):
                raise ProviderProtocolError("Anthropic text delta must be text")
            return ModelEvent(kind=ModelEventKind.TEXT_DELTA, text=text)
        if delta_type == "thinking_delta":
            thinking = delta.get("thinking")
            if not isinstance(thinking, str):
                raise ProviderProtocolError("Anthropic thinking delta must be text")
            return ModelEvent(kind=ModelEventKind.REASONING_DELTA, text=thinking)
        if delta_type == "input_json_delta":
            index = cls._index(value)
            fragment = delta.get("partial_json")
            if index not in pending or not isinstance(fragment, str):
                raise ProviderProtocolError("Anthropic tool input delta is invalid")
            pending[index].fragments.append(fragment)
        return None

    @staticmethod
    def _index(value: Mapping[str, object]) -> int:
        index = value.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ProviderProtocolError("Anthropic content block index is invalid")
        return index
