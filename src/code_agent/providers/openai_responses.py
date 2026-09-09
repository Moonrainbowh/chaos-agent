from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Optional

import httpx

from code_agent.core.models import Message, ModelEvent, ModelEventKind, ToolCall, ToolDefinition, Usage

from ._limits import ArgumentBuffer, ToolBudget
from .attachments import AttachmentResolver, ProviderAttachmentEncoder
from .config import ApiProtocol, InputModality, ProviderConfig
from .errors import ProviderConfigError, ProviderProtocolError
from ._request_payload import request_options, responses_payload
from .transport import ProviderTransport, Sleep
def _request_input(
    messages: Sequence[Message], encoder: ProviderAttachmentEncoder
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for message in messages:
        if message.role == "tool":
            if message.tool_call_id is None:
                raise ProviderConfigError("Responses tool messages require tool_call_id")
            result.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content,
                }
            )
            continue
        if message.content or not message.tool_calls:
            item: dict[str, object] = {
                "role": message.role,
                "content": encoder.responses(message),
            }
            if message.name is not None:
                item["name"] = message.name
            result.append(item)
        for call in message.tool_calls:
            result.append(
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": json.dumps(call.to_dict()["arguments"], separators=(",", ":")),
                }
            )
    return result
def _request_tool(tool: ToolDefinition) -> dict[str, object]:
    value = tool.to_dict()
    return {
        "type": "function",
        "name": value["name"],
        "description": value["description"],
        "parameters": value["parameters"],
    }


def _load_event(data: str) -> Mapping[str, object]:
    try:
        value = json.loads(data)
    except (json.JSONDecodeError, UnicodeError):
        raise ProviderProtocolError("Responses stream contains malformed JSON") from None
    if not isinstance(value, dict):
        raise ProviderProtocolError("Responses event must be a JSON object")
    return value

def _terminal_error(event_type: str, value: Mapping[str, object]) -> ProviderProtocolError:
    response = value.get("response")
    key = "error" if event_type == "response.failed" else "incomplete_details"
    details = response.get(key) if isinstance(response, dict) else None
    suffix = ""
    if isinstance(details, dict):
        for label in ("code", "reason", "type"):
            detail = details.get(label)
            if isinstance(detail, str) and detail:
                excerpt = " ".join(detail.splitlines())[:120]
                suffix = f"; {label}={excerpt}"
                break
    return ProviderProtocolError(f"Responses terminal event: {event_type}{suffix}")

@dataclass(eq=False)
class _CallState:
    arguments: ArgumentBuffer
    item_id: str = ""
    call_id: str = ""
    name: str = ""
    emitted: bool = False

class _CallRegistry:
    def __init__(self, budget: ToolBudget) -> None:
        self.states: list[_CallState] = []
        self._aliases: dict[str, _CallState] = {}
        self._budget = budget

    def resolve(self, event: Mapping[str, object], item: Optional[Mapping[str, object]] = None) -> _CallState:
        keys = self._keys(event, item)
        matches = {self._aliases[key] for key in keys if key in self._aliases}
        if len(matches) > 1:
            raise ProviderProtocolError("Responses tool call aliases conflict")
        if matches:
            state = matches.pop()
        else:
            state = _CallState(arguments=self._budget.new_arguments())
            self.states.append(state)
        for key in keys:
            self._aliases[key] = state
        self._update_identity(state, event)
        if item is not None:
            self._update_identity(state, item)
        return state

    @staticmethod
    def _keys(
        event: Mapping[str, object], item: Optional[Mapping[str, object]]
    ) -> list[str]:
        keys: list[str] = []
        for value in (event, item or {}):
            for field, prefix in (
                ("id", "item"),
                ("item_id", "item"),
                ("call_id", "call"),
                ("output_index", "index"),
            ):
                part = value.get(field)
                if isinstance(part, (str, int)) and not isinstance(part, bool):
                    keys.append(f"{prefix}:{part}")
        if not keys:
            raise ProviderProtocolError("Responses tool call has no stable identifier")
        return keys

    @staticmethod
    def _update_identity(state: _CallState, value: Mapping[str, object]) -> None:
        item_id = value.get("item_id", value.get("id"))
        call_id = value.get("call_id")
        name = value.get("name")
        for part, label in ((item_id, "item id"), (call_id, "call id"), (name, "name")):
            if part is not None and not isinstance(part, str):
                raise ProviderProtocolError(f"Responses tool {label} must be text")
        if item_id:
            state.item_id = item_id  # type: ignore[assignment]
        if call_id:
            state.call_id = call_id  # type: ignore[assignment]
        if name:
            state.name = name  # type: ignore[assignment]

def _finalize(state: _CallState) -> Optional[ModelEvent]:
    if state.emitted:
        return None
    try:
        arguments = json.loads(state.arguments.text())
        if not isinstance(arguments, dict):
            raise TypeError("arguments are not an object")
        call = ToolCall(id=state.call_id or state.item_id, name=state.name, arguments=arguments)
    except (json.JSONDecodeError, TypeError, ValueError):
        raise ProviderProtocolError("Responses tool arguments are malformed") from None
    state.emitted = True
    return ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=call)

def _usage(value: object) -> ModelEvent:
    if not isinstance(value, dict):
        raise ProviderProtocolError("Responses usage must be a JSON object")
    details = value.get("input_tokens_details", {})
    if not isinstance(details, dict):
        raise ProviderProtocolError("Responses usage details must be an object")
    try:
        usage = Usage(
            input_tokens=value.get("input_tokens", 0),  # type: ignore[arg-type]
            output_tokens=value.get("output_tokens", 0),  # type: ignore[arg-type]
            cached_input_tokens=details.get("cached_tokens", 0),  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as error:
        raise ProviderProtocolError("Responses usage has invalid token counts") from error
    return ModelEvent(kind=ModelEventKind.USAGE, usage=usage)
class OpenAIResponsesClient:
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
        if config.api not in {ApiProtocol.RESPONSES, ApiProtocol.CODEX_RESPONSES}:
            raise ProviderConfigError("OpenAIResponsesClient requires responses API")
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

    async def stream(self, system_prompt: str, messages: Sequence[Message], tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[ModelEvent]:
        payload = responses_payload(
            self._config, system_prompt, _request_input(messages, self._attachments),
            [_request_tool(tool) for tool in tools],
            self._request_options,
        )
        calls = _CallRegistry(
            ToolBudget(self._config.max_tool_calls, self._config.max_tool_argument_bytes)
        )
        async for sse in self._transport.stream_sse(
            self._config.responses_path, payload):
            value = _load_event(sse.data)
            event_type = value.get("type", sse.event)
            if not isinstance(event_type, str):
                continue
            if event_type == "error" or sse.event == "error":
                raise ProviderProtocolError("Responses provider returned an error event")
            if event_type in {"response.failed", "response.incomplete"}:
                raise _terminal_error(event_type, value)
            if event_type == "response.output_text.delta":
                yield self._text_delta(value, ModelEventKind.TEXT_DELTA)
            elif event_type in {"response.reasoning.delta", "response.reasoning_text.delta",
                                "response.reasoning_summary_text.delta"}:
                yield self._text_delta(value, ModelEventKind.REASONING_DELTA)
            elif event_type == "response.output_item.added":
                self._item_added(value, calls)
            elif event_type == "response.function_call_arguments.delta":
                state = calls.resolve(value)
                delta = value.get("delta")
                if not isinstance(delta, str):
                    raise ProviderProtocolError("Responses argument delta must be text")
                state.arguments.append(delta)
            elif event_type == "response.output_item.done":
                result = self._item_done(value, calls)
                if result is not None:
                    yield result
            elif event_type == "response.completed":
                for state in calls.states:
                    result = _finalize(state)
                    if result is not None:
                        yield result
                response = value.get("response", {})
                if not isinstance(response, dict):
                    raise ProviderProtocolError("Completed response must be an object")
                usage = response.get("usage", value.get("usage"))
                if usage is not None:
                    yield _usage(usage)
                yield ModelEvent(kind=ModelEventKind.COMPLETED)
                return
        raise ProviderProtocolError("Responses stream ended without response.completed")
    @staticmethod
    def _text_delta(value: Mapping[str, object], kind: ModelEventKind) -> ModelEvent:
        delta = value.get("delta")
        if not isinstance(delta, str):
            raise ProviderProtocolError("Responses text delta must be text")
        return ModelEvent(kind=kind, text=delta)

    @staticmethod
    def _item_added(value: Mapping[str, object], calls: _CallRegistry) -> None:
        item = value.get("item")
        if not isinstance(item, dict):
            raise ProviderProtocolError("Responses output item must be an object")
        if item.get("type") != "function_call":
            return
        state = calls.resolve(value, item)
        arguments = item.get("arguments", "")
        if not isinstance(arguments, str):
            raise ProviderProtocolError("Responses tool arguments must be text")
        if arguments and state.arguments.is_empty:
            state.arguments.append(arguments)

    @staticmethod
    def _item_done(
        value: Mapping[str, object], calls: _CallRegistry
    ) -> Optional[ModelEvent]:
        item = value.get("item")
        if not isinstance(item, dict):
            raise ProviderProtocolError("Responses completed item must be an object")
        if item.get("type") != "function_call":
            return None
        state = calls.resolve(value, item)
        arguments = item.get("arguments")
        if arguments is not None:
            if not isinstance(arguments, str):
                raise ProviderProtocolError("Responses tool arguments must be text")
            state.arguments.replace(arguments)
        return _finalize(state)
