"""Gemini streaming adapter; Antigravity envelope follows URI Agent (MIT)."""
from __future__ import annotations

import asyncio
import json
import uuid
from urllib.parse import quote

from code_agent.core.models import ModelEvent, ModelEventKind, ToolCall, Usage
from .attachments import ProviderAttachmentEncoder
from .config import ApiProtocol, InputModality
from .errors import ProviderConfigError, ProviderProtocolError
from ._limits import ToolBudget
from ._request_payload import request_options
from .transport import ProviderTransport


def google_messages(system, messages, encoder):
    contents, systems, names = [], [system] if system else [], {}
    for message in messages:
        if message.role in {"system", "developer"}:
            systems.append(message.content)
            continue
        if message.role == "tool":
            name = message.name or names.get(message.tool_call_id)
            if not name:
                raise ProviderConfigError("Google tool result requires a correlated tool name")
            parts = [{"functionResponse": {"name": name, "response": {"output": message.content}}}]
        else:
            encoded = encoder.anthropic(message)
            blocks = encoded if isinstance(encoded, list) else [{"type": "text", "text": encoded}]
            parts = []
            for block in blocks:
                if block["type"] == "text":
                    parts.append({"text": block["text"]})
                else:
                    source = block["source"]
                    parts.append({"inlineData": {"mimeType": source["media_type"], "data": source["data"]}})
            for call in message.tool_calls:
                names[call.id] = call.name
                parts.append({"functionCall": {"name": call.name, "args": call.to_dict()["arguments"]}})
        role = "model" if message.role == "assistant" else "user"
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})
    return "\n\n".join(systems), contents


class GoogleGenerativeAIClient:
    def __init__(self, config, *, attachment_resolver=None,
                 input_modalities=(InputModality.TEXT,), http_client=None,
                 sleep=asyncio.sleep, reasoning_effort=None, max_output_tokens=4096):
        if config.api is not ApiProtocol.GOOGLE_GENERATIVE_AI:
            raise ProviderConfigError("GoogleGenerativeAIClient requires google_generative_ai")
        self._config = config
        self._attachments = ProviderAttachmentEncoder(attachment_resolver, input_modalities)
        self._options = request_options(config.api, reasoning_effort=reasoning_effort,
                                        max_output_tokens=max_output_tokens)
        self._transport = ProviderTransport(config, client=http_client, sleep=sleep)

    async def aclose(self):
        await self._transport.aclose()

    async def stream(self, system_prompt, messages, tools):
        system, contents = google_messages(system_prompt, messages, self._attachments)
        generation = {"maxOutputTokens": self._options.max_output_tokens}
        if self._options.reasoning_effort:
            generation["thinkingConfig"] = _thinking(self._config.model, self._options)
        body = {"contents": contents, "generationConfig": generation}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{"functionDeclarations": [tool.to_dict() for tool in tools]}]
        budget = ToolBudget(self._config.max_tool_calls, self._config.max_tool_argument_bytes)
        path = f"/models/{quote(self._config.model, safe='')}:streamGenerateContent?alt=sse"
        finished = False
        async for sse in self._transport.stream_sse(path, body, auth_header="x-goog-api-key", auth_scheme=None):
            value = _object(sse.data)
            value = value.get("response", value)
            if not isinstance(value, dict) or "error" in value:
                raise ProviderProtocolError("Google provider returned an invalid or error event")
            candidates = value.get("candidates", [])
            if not isinstance(candidates, list) or len(candidates) > 1:
                raise ProviderProtocolError("Google stream must contain at most one candidate")
            for candidate in candidates:
                if not isinstance(candidate, dict) or not isinstance(candidate.get("content", {}), dict):
                    raise ProviderProtocolError("Google candidate is malformed")
                parts = candidate.get("content", {}).get("parts", [])
                if not isinstance(parts, list):
                    raise ProviderProtocolError("Google content parts are malformed")
                for part in parts:
                    event = _part_event(part, budget)
                    if event:
                        yield event
                reason = candidate.get("finishReason")
                if reason:
                    if reason != "STOP":
                        raise ProviderProtocolError("Google generation ended without normal completion")
                    finished = True
            if "usageMetadata" in value:
                yield _usage(value["usageMetadata"])
        if not finished:
            raise ProviderProtocolError("Google stream ended without finishReason")
        yield ModelEvent(kind=ModelEventKind.COMPLETED)


def _object(data):
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        raise ProviderProtocolError("Google stream contains malformed JSON") from None
    if not isinstance(value, dict):
        raise ProviderProtocolError("Google event must be an object")
    return value


def _thinking(model, options):
    """Use URI's documented Gemini family mapping; never infer unknown families."""
    model = model.lower()
    effort = options.reasoning_effort
    if model.startswith("gemini-3") or "gemma-4" in model or "gemma4" in model:
        level = "low" if effort == "low" else ("medium" if effort == "medium" else "high")
        if "pro" in model and level == "medium":
            level = "high"
        return {"thinkingLevel": level.upper(), "includeThoughts": True}
    if "gemini-2.5" in model:
        budget = 2048 if effort == "low" else 8192 if effort == "medium" else 32768 if "pro" in model else 24576
        if budget >= options.max_output_tokens:
            raise ProviderConfigError("Google thinking budget must be below max_output_tokens")
        return {"thinkingBudget": budget, "includeThoughts": True}
    raise ProviderConfigError("Google reasoning mapping is unknown for this model")


def _part_event(part, budget):
    if not isinstance(part, dict):
        raise ProviderProtocolError("Google content part is malformed")
    if "text" in part:
        if not isinstance(part["text"], str):
            raise ProviderProtocolError("Google text must be a string")
        kind = ModelEventKind.REASONING_DELTA if part.get("thought") else ModelEventKind.TEXT_DELTA
        return ModelEvent(kind=kind, text=part["text"])
    if "functionCall" in part:
        call = part["functionCall"]
        if not isinstance(call, dict):
            raise ProviderProtocolError("Google function call must be an object")
        args = call.get("args", {})
        buffer = budget.new_arguments()
        buffer.append(json.dumps(args))
        try:
            tool = ToolCall(id=call.get("id") or f"google_call_{uuid.uuid4().hex}", name=call["name"], arguments=args)
        except (TypeError, ValueError, KeyError):
            raise ProviderProtocolError("Google tool call is malformed") from None
        return ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=tool)
    return None


def _usage(value):
    try:
        for key in ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "cachedContentTokenCount"):
            count = value.get(key, 0)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("invalid token count")
        usage = Usage(input_tokens=value.get("promptTokenCount", 0),
                      output_tokens=value.get("candidatesTokenCount", 0) + value.get("thoughtsTokenCount", 0),
                      cached_input_tokens=value.get("cachedContentTokenCount", 0))
    except (AttributeError, TypeError, ValueError):
        raise ProviderProtocolError("Google usage is malformed") from None
    return ModelEvent(kind=ModelEventKind.USAGE, usage=usage)
