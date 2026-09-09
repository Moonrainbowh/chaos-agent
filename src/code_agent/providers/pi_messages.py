"""Native pi message wire protocol, adapted from URI Agent (MIT)."""
from __future__ import annotations

import asyncio
import json

from code_agent.core.models import ModelEvent, ModelEventKind, ToolCall, Usage
from .attachments import ProviderAttachmentEncoder
from .config import ApiProtocol, InputModality
from .errors import ProviderConfigError, ProviderProtocolError
from ._limits import ToolBudget
from ._request_payload import request_options
from .transport import ProviderTransport


def _messages(system, messages, encoder, config):
    systems, output, names = [system] if system else [], [], {}
    for message in messages:
        if message.role in {"system", "developer"}:
            systems.append(message.content)
            continue
        encoded = encoder.anthropic(message)
        blocks = encoded if isinstance(encoded, list) else [{"type": "text", "text": encoded}]
        content = []
        for block in blocks:
            if block["type"] == "image":
                source = block["source"]
                content.append({"type": "image", "data": source["data"], "mimeType": source["media_type"]})
            else:
                content.append(block)
        item = {"role": message.role, "content": content, "timestamp": 0}
        if message.role == "tool":
            name = message.name or names.get(message.tool_call_id)
            if not message.tool_call_id or not name:
                raise ProviderConfigError("pi tool result requires a correlated tool name")
            item.update(role="toolResult", toolCallId=message.tool_call_id, toolName=name, isError=False)
        if message.role == "assistant":
            for call in message.tool_calls:
                names[call.id] = call.name
                content.append({"type": "toolCall", **call.to_dict()})
            item.update(api="pi-messages", provider=config.provider_id or "", model=config.model,
                        usage={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0,
                               "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0}},
                        stopReason="toolUse" if message.tool_calls else "stop")
        output.append(item)
    return "\n\n".join(systems), output


class PiMessagesClient:
    def __init__(self, config, *, attachment_resolver=None,
                 input_modalities=(InputModality.TEXT,), http_client=None,
                 sleep=asyncio.sleep, reasoning_effort=None, max_output_tokens=4096):
        if config.api is not ApiProtocol.PI_MESSAGES:
            raise ProviderConfigError("PiMessagesClient requires pi_messages")
        self._config = config
        self._attachments = ProviderAttachmentEncoder(attachment_resolver, input_modalities)
        self._options = request_options(config.api, reasoning_effort=reasoning_effort,
                                        max_output_tokens=max_output_tokens)
        self._transport = ProviderTransport(config, client=http_client, sleep=sleep)

    async def aclose(self):
        await self._transport.aclose()

    async def stream(self, system_prompt, messages, tools):
        system, history = _messages(system_prompt, messages, self._attachments, self._config)
        options = {"maxTokens": self._options.max_output_tokens}
        if self._options.reasoning_effort:
            options["reasoning"] = self._options.reasoning_effort
        body = {"model": self._config.model, "options": options,
                "context": {"systemPrompt": system, "messages": history,
                            "tools": [tool.to_dict() for tool in tools]}}
        state = _StreamState(self._config)
        async for sse in self._transport.stream_sse(self._config.pi_messages_path, body):
            try:
                value = json.loads(sse.data)
            except (ValueError, UnicodeError):
                raise ProviderProtocolError("pi stream contains malformed JSON") from None
            if not isinstance(value, dict):
                raise ProviderProtocolError("pi event must be an object")
            for event in state.consume(value):
                yield event
                if event.kind is ModelEventKind.COMPLETED:
                    return
        raise ProviderProtocolError("pi stream ended without done")


class _StreamState:
    def __init__(self, config):
        self.budget = ToolBudget(config.max_tool_calls, config.max_tool_argument_bytes)
        self.blocks = {}
        self.tools = {}
        self.emitted = set()

    def consume(self, value):
        kind = value.get("type")
        if kind == "error":
            raise ProviderProtocolError("pi provider returned an error event")
        if kind == "done":
            if any(index not in self.emitted for index in self.tools):
                raise ProviderProtocolError("pi stream ended with an unfinished tool call")
            if value.get("reason") not in {"stop", "toolUse"}:
                raise ProviderProtocolError("pi stream ended without normal completion")
            if "usage" in value:
                yield _usage(value["usage"])
            yield ModelEvent(kind=ModelEventKind.COMPLETED)
            return
        if kind == "start":
            return
        if not isinstance(kind, str) or "_" not in kind:
            raise ProviderProtocolError("pi event type is invalid")
        block, phase = kind.rsplit("_", 1)
        if block not in {"text", "thinking", "toolcall"}:
            return
        index = value.get("contentIndex")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ProviderProtocolError("pi event has invalid contentIndex")
        if self.blocks.setdefault(index, block) != block:
            raise ProviderProtocolError("pi delta does not match its content block")
        if block == "toolcall":
            yield from self._tool(value, index, phase)
        elif phase == "delta":
            delta = value.get("delta")
            if not isinstance(delta, str):
                raise ProviderProtocolError("pi delta must be text")
            event_kind = ModelEventKind.TEXT_DELTA if block == "text" else ModelEventKind.REASONING_DELTA
            yield ModelEvent(kind=event_kind, text=delta)

    def _tool(self, value, index, phase):
        if index not in self.tools:
            self.tools[index] = self.budget.new_arguments()
        if phase == "delta":
            delta = value.get("delta")
            if not isinstance(delta, str):
                raise ProviderProtocolError("pi tool delta must be text")
            self.tools[index].append(delta)
        elif phase == "end":
            if index in self.emitted:
                raise ProviderProtocolError("pi tool call ended twice")
            try:
                call = value["toolCall"]
                self.tools[index].replace(json.dumps(call["arguments"]))
                tool = ToolCall(id=call["id"], name=call["name"], arguments=call["arguments"])
            except (KeyError, TypeError, ValueError):
                raise ProviderProtocolError("pi tool call is malformed") from None
            self.emitted.add(index)
            yield ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=tool)


def _usage(value):
    try:
        usage = Usage(input_tokens=value.get("input", 0), output_tokens=value.get("output", 0),
                      cached_input_tokens=value.get("cacheRead", 0))
    except (AttributeError, TypeError, ValueError):
        raise ProviderProtocolError("pi usage is malformed") from None
    return ModelEvent(kind=ModelEventKind.USAGE, usage=usage)
